import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from .config import *
from .memory import memory_store
from .dispatcher import send_msg
from .tool_registry import registry as tool_registry

# Require runtime locks/structures to be provided by config; import explicitly
try:
    from .config import (
        _ACTIVE_STREAMS,
        _ACTIVE_STREAMS_LOCK,
        _RATE_LOCK,
        _HASHES_LOCK,
        _EXECUTION_STATE_LOCK,
        _SEND_LOCK,
        _BOT_STATE_LOCK,
        CEREBRAS_API_KEY,
        _LLM_CLIENT,
        LLM_MODEL,
        CEREBRAS_MODEL,
    )
except Exception:
    try:
        # fallback to testing.host.config absolute import
        from testing.host.config import (
            _ACTIVE_STREAMS,
            _ACTIVE_STREAMS_LOCK,
            _RATE_LOCK,
            _HASHES_LOCK,
            _EXECUTION_STATE_LOCK,
            _SEND_LOCK,
            _BOT_STATE_LOCK,
            CEREBRAS_API_KEY,
            _LLM_CLIENT,
            LLM_MODEL,
            CEREBRAS_MODEL,
        )
    except Exception as e:
        raise RuntimeError("streaming requires proper initialization of config locks and client: " + str(e))

# Ensure _atomic_write_json is available from persistence_helpers
try:
    from .persistence_helpers import _atomic_write_json
except Exception:
    try:
        from persistence_helpers import _atomic_write_json
    except Exception as e:
        raise RuntimeError("streaming requires persistence_helpers._atomic_write_json: " + str(e))


def format_prefetch_tool_block(
    *,
    graph=None,
    cell_slice: str | None = None,
    placement: dict | None = None,
) -> str:
    """Build prefetched local-tool evidence as markdown (for system or current-turn tail)."""
    parts: list[str] = []
    if graph is not None:
        parts.append(
            "### notebook_graph_query (prefetched)\n"
            + json.dumps(graph, ensure_ascii=False)
        )
    if cell_slice:
        parts.append(f"### notebook_get_cell (prefetched)\n{cell_slice}")
    if placement is not None:
        parts.append(
            "### notebook_recommend_placement (prefetched — follow this for Placement section)\n"
            + json.dumps(placement, ensure_ascii=False)
        )
    if not parts:
        return ""
    return (
        "## Prefetched tool results\n"
        "Use this evidence for your answer. For placement, follow `notebook_recommend_placement` "
        "(insert NEW code cell below the defining cell — not a distant empty cell).\n\n"
        + "\n\n".join(parts)
    )


def inject_prefetched_tool_context(
    messages: list,
    *,
    graph=None,
    cell_slice: str | None = None,
    placement: dict | None = None,
    target: str = "auto",
) -> None:
    """
    Attach eager tool results without role=tool (Cerebras requires tool_call_id for that).

    target=tail keeps the system prefix stable for Cerebras prompt caching; target=system
    merges into system content (legacy path when static notebook cache is off).
    """
    block = format_prefetch_tool_block(
        graph=graph,
        cell_slice=cell_slice,
        placement=placement,
    )
    if not block:
        return

    use_tail = target == "tail"
    if target == "auto":
        try:
            from .prompt_cache_baseline import cerebras_static_cache_enabled
        except Exception:
            from prompt_cache_baseline import cerebras_static_cache_enabled
        use_tail = cerebras_static_cache_enabled()

    if use_tail:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                prev = str(messages[i].get("content") or "").strip()
                messages[i]["content"] = f"{block}\n\n---\n\n{prev}" if prev else block
                return
        messages.append({"role": "user", "content": block})
        return

    for msg in messages:
        if msg.get("role") == "system":
            msg["content"] = str(msg.get("content", "")).rstrip() + "\n\n" + block
            return

    messages.insert(0, {"role": "system", "content": block})


def _load_rate_tracker() -> dict:
    if not RATE_LIMIT_TRACKER.exists():
        return {"events": []}
    try:
        data = json.loads(RATE_LIMIT_TRACKER.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"events": []}
        legacy = data.get("requests", []) if isinstance(data.get("requests", []), list) else []
        events = data.get("events", []) if isinstance(data.get("events", []), list) else []
        if legacy and not events:
            for item in legacy:
                ts = item.get("timestamp")
                if ts:
                    events.append({
                        "id": str(uuid.uuid4()),
                        "timestamp": ts,
                        "tokens": int(item.get("tokens", 0) or 0),
                        "requests": int(item.get("requests", 1) or 1),
                    })
        data["events"] = events
        return data
    except Exception:
        return {"events": []}


def _save_rate_tracker(data: dict):
    _atomic_write_json(RATE_LIMIT_TRACKER, data)


def _prune_rate_tracker(data: dict) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    pruned = []
    for event in data.get("events", []):
        try:
            ts = datetime.fromisoformat(str(event.get("timestamp", "")))
        except Exception:
            continue
        if ts >= cutoff:
            pruned.append(event)
    data["events"] = pruned
    return data


def _record_request_attempt(attempt_id: str):
    with _RATE_LOCK:
        tracker = _prune_rate_tracker(_load_rate_tracker())
        tracker.setdefault("events", []).append({
            "id": attempt_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tokens": 0,
            "requests": 1,
        })
        _save_rate_tracker(tracker)


def _compact_tool_result_content(content: str) -> str:
    try:
        from .config import MAX_TOOL_RESULT_CHARS
    except Exception:
        from config import MAX_TOOL_RESULT_CHARS
    text = str(content or "")
    limit = int(MAX_TOOL_RESULT_CHARS or 0)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n...[tool result truncated]"


def _finalize_request_attempt(
    attempt_id: str,
    tokens: int = 0,
    *,
    usage: dict | None = None,
):
    try:
        from .token_usage import billable_tokens
    except Exception:
        from token_usage import billable_tokens
    billable = billable_tokens(usage, tokens)
    with _RATE_LOCK:
        tracker = _prune_rate_tracker(_load_rate_tracker())
        for event in reversed(tracker.get("events", [])):
            if event.get("id") == attempt_id:
                event["tokens"] = int(billable)
                if usage:
                    event["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
                    event["completion_tokens"] = int(usage.get("completion_tokens") or 0)
                    event["cached_tokens"] = int(usage.get("cached_tokens") or 0)
                break
        _save_rate_tracker(tracker)


def _record_llm_usage(
    *,
    attempt_id: str,
    usage: dict | None,
    estimated_tokens: int,
    session_id: str,
    history_key: str,
    mode: str,
    label: str,
    turn_usage: dict | None,
) -> None:
    try:
        from .token_usage import (
            billable_tokens,
            extract_usage_from_response,
            format_usage_line,
            merge_usage,
            record_token_event,
        )
    except Exception:
        from token_usage import (
            billable_tokens,
            extract_usage_from_response,
            format_usage_line,
            merge_usage,
            record_token_event,
        )
    parsed = usage or {}
    if not parsed.get("total_tokens"):
        parsed = {}
    billable = billable_tokens(parsed, estimated_tokens)
    log(format_usage_line(parsed if parsed else {}, estimated=estimated_tokens if not parsed else None))
    record_token_event(
        attempt_id=attempt_id,
        session_id=session_id,
        history_key=history_key,
        mode=mode,
        label=label,
        usage=parsed or None,
        estimated_tokens=estimated_tokens,
    )
    _finalize_request_attempt(attempt_id, billable, usage=parsed or None)
    if turn_usage is not None:
        merge_usage(turn_usage, parsed if parsed else {"total_tokens": billable})


_LAST_LLM_SPACING_LOCK = threading.Lock()
_LAST_LLM_SPACING_MONO = 0.0


def _rate_usage(events: list):
    now = datetime.now(timezone.utc)
    one_min = now - timedelta(minutes=1)
    one_hour = now - timedelta(hours=1)
    one_day = now - timedelta(hours=24)

    tpm = rpm = tph = rph = tpd = rpd = 0
    for event in events:
        try:
            ts = datetime.fromisoformat(str(event.get("timestamp", "")))
        except Exception:
            continue
        tokens = int(event.get("tokens", 0) or 0)
        reqs = int(event.get("requests", 1) or 1)
        if ts >= one_min:
            tpm += tokens
            rpm += reqs
        if ts >= one_hour:
            tph += tokens
            rph += reqs
        if ts >= one_day:
            tpd += tokens
            rpd += reqs
    return tpm, rpm, tph, rph, tpd, rpd


def _wait_for_request_slot(cancel_check=None):
    while True:
        if callable(cancel_check) and cancel_check():
            return False
        with _RATE_LOCK:
            tracker = _prune_rate_tracker(_load_rate_tracker())
            events = tracker.get("events", [])
            now = datetime.now(timezone.utc)
            one_min = now - timedelta(minutes=1)
            one_hour = now - timedelta(hours=1)
            one_day = now - timedelta(hours=24)

            rpm = sum(1 for e in events if datetime.fromisoformat(str(e.get("timestamp", ""))) >= one_min)
            rph = sum(1 for e in events if datetime.fromisoformat(str(e.get("timestamp", ""))) >= one_hour)
            rpd = sum(1 for e in events if datetime.fromisoformat(str(e.get("timestamp", ""))) >= one_day)

            if rpm < RPM_LIMIT and rph < RPH_LIMIT and rpd < RPD_LIMIT:
                _save_rate_tracker(tracker)
                return True

            waits = []
            if rpm >= RPM_LIMIT:
                oldest = min(datetime.fromisoformat(e["timestamp"]) for e in events if datetime.fromisoformat(e["timestamp"]) >= one_min)
                waits.append((oldest + timedelta(minutes=1) - now).total_seconds())
            if rph >= RPH_LIMIT:
                oldest = min(datetime.fromisoformat(e["timestamp"]) for e in events if datetime.fromisoformat(e["timestamp"]) >= one_hour)
                waits.append((oldest + timedelta(hours=1) - now).total_seconds())
            if rpd >= RPD_LIMIT:
                oldest = min(datetime.fromisoformat(e["timestamp"]) for e in events if datetime.fromisoformat(e["timestamp"]) >= one_day)
                waits.append((oldest + timedelta(hours=24) - now).total_seconds())

        sleep_for = max(0.1, max(waits) if waits else 0.1)
        log(f"Rate limit slot wait: {sleep_for:.2f}s")
        time.sleep(sleep_for)


def _check_token_limits() -> tuple[bool, str]:
    with _RATE_LOCK:
        tracker = _prune_rate_tracker(_load_rate_tracker())
        tpm, rpm, tph, rph, tpd, rpd = _rate_usage(tracker.get("events", []))
        _save_rate_tracker(tracker)

    violations = []
    if tpm >= TPM_LIMIT:
        violations.append(f"TPM {tpm}/{TPM_LIMIT}")
    if tph >= TPH_LIMIT:
        violations.append(f"TPH {tph}/{TPH_LIMIT}")
    if tpd >= TPD_LIMIT:
        violations.append(f"TPD {tpd}/{TPD_LIMIT}")
    if rpm >= RPM_LIMIT:
        violations.append(f"RPM {rpm}/{RPM_LIMIT}")
    if rph >= RPH_LIMIT:
        violations.append(f"RPH {rph}/{RPH_LIMIT}")
    if rpd >= RPD_LIMIT:
        violations.append(f"RPD {rpd}/{RPD_LIMIT}")
    if violations:
        return False, " | ".join(violations)
    return True, ""


def _extract_delta_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out = []
        for part in value:
            if isinstance(part, dict):
                t = part.get("text") or part.get("content") or ""
            else:
                t = getattr(part, "text", None) or getattr(part, "content", "")
            t = _extract_delta_text(t)
            if t:
                out.append(t)
        return "".join(out)
    if isinstance(value, dict):
        return _extract_delta_text(value.get("text") or value.get("content") or "")
    return str(getattr(value, "text", None) or getattr(value, "content", "") or "")


def _chunk_text_from_event(event) -> str:
    try:
        choices = getattr(event, "choices", None) or []
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            return ""
        return _extract_delta_text(getattr(delta, "content", None))
    except Exception:
        return ""


def _completion_extra_kwargs(*, session_id: str | None = None, mode: str | None = None) -> dict:
    """Provider-specific API options (Cerebras reasoning + prompt cache)."""
    try:
        from .config import LLM_PROVIDER
    except Exception:
        from config import LLM_PROVIDER

    if str(LLM_PROVIDER or "").lower() != "cerebras":
        return {}

    try:
        from .llm_provider import cerebras_completion_extras
    except Exception:
        from llm_provider import cerebras_completion_extras
    return cerebras_completion_extras(session_id=session_id, mode=mode)


def _llm_api_label() -> str:
    try:
        from .llm_provider import provider_display_name
        from .config import LLM_PROVIDER
    except Exception:
        from llm_provider import provider_display_name
        from config import LLM_PROVIDER
    return provider_display_name(LLM_PROVIDER)


def _parallel_tool_calls_flag(*, agentic: bool = False) -> bool:
    try:
        from .llm_provider import parallel_tool_calls_enabled
        from .config import LLM_PROVIDER
    except Exception:
        from llm_provider import parallel_tool_calls_enabled
        from config import LLM_PROVIDER
    return parallel_tool_calls_enabled(LLM_PROVIDER, agentic=agentic)


def _llm_react_throttle() -> None:
    """Enforce minimum spacing between LLM calls (5/min for Cerebras)."""
    try:
        from .llm_provider import react_min_interval_sec
        from .config import LLM_PROVIDER
    except Exception:
        from llm_provider import react_min_interval_sec
        from config import LLM_PROVIDER
    delay = react_min_interval_sec(LLM_PROVIDER)
    if delay <= 0:
        return
    global _LAST_LLM_SPACING_MONO
    with _LAST_LLM_SPACING_LOCK:
        now = time.monotonic()
        wait = (_LAST_LLM_SPACING_MONO + delay) - now
        if wait > 0:
            log(f"LLM spacing throttle: {wait:.1f}s")
            time.sleep(wait)
        _LAST_LLM_SPACING_MONO = time.monotonic()


def _begin_llm_request(cancel_check=None) -> str | None:
    """Wait for RPM slot, apply spacing throttle, record attempt."""
    if not _wait_for_request_slot(cancel_check):
        return None
    _llm_react_throttle()
    attempt_id = str(uuid.uuid4())
    _record_request_attempt(attempt_id)
    return attempt_id


def _preflight_prompt_budget(estimated_tokens: int) -> tuple[bool, str]:
    try:
        from .config import ENABLE_TPM_PREFLIGHT
    except Exception:
        from config import ENABLE_TPM_PREFLIGHT
    if not ENABLE_TPM_PREFLIGHT:
        return True, ""
    allowed, details = _check_token_limits()
    if not allowed:
        return False, details
    budget = int(TPM_LIMIT * TPM_PREFLIGHT_RATIO)
    with _RATE_LOCK:
        tracker = _prune_rate_tracker(_load_rate_tracker())
        tpm, _, _, _, _, _ = _rate_usage(tracker.get("events", []))
    projected = tpm + max(0, int(estimated_tokens))
    if projected > budget:
        return (
            False,
            f"Prompt uses ~{estimated_tokens} tokens; rolling minute usage {tpm}/{TPM_LIMIT}. "
            f"Wait ~60s and retry, or set CONTEXT_PACK_MODE=intent / lower MAX_CELL_OUTPUT_CHARS.",
        )
    return True, ""


def _final_text_from_response(response) -> str:
    def _extract_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            out = []
            for part in value:
                if isinstance(part, dict):
                    t = part.get("text") or part.get("content") or ""
                else:
                    t = getattr(part, "text", None) or getattr(part, "content", "")
                t = _extract_text(t)
                if t:
                    out.append(t)
            return "".join(out)
        if isinstance(value, dict):
            return _extract_text(value.get("text") or value.get("content") or "")
        return str(getattr(value, "text", None) or getattr(value, "content", "") or "")

    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            if hasattr(response, "model_dump"):
                dumped = response.model_dump()
                d_choices = dumped.get("choices") or []
                if d_choices:
                    msg = d_choices[0].get("message") or {}
                    return _extract_text(msg.get("content"))
            return ""
        message = getattr(choices[0], "message", None)
        if message is not None:
            return _extract_text(getattr(message, "content", None))
        if hasattr(response, "model_dump"):
            dumped = response.model_dump()
            d_choices = dumped.get("choices") or []
            if d_choices:
                msg = d_choices[0].get("message") or {}
                return _extract_text(msg.get("content"))
        return ""
    except Exception:
        return ""


def _close_stream_handle(stream):
    if stream is None:
        return
    for attr in ("close", "aclose", "cancel"):
        try:
            closer = getattr(stream, attr, None)
            if callable(closer):
                closer()
                return
        except Exception:
            continue


def _stop_active_stream(state: dict | None):
    if not state:
        return
    state["stopped"] = True
    _close_stream_handle(state.get("stream"))


def resolve_active_key(tab_id, stream_channel: str | None = None) -> str:
    """Isolate concurrent streams on the same tab (main copilot vs per-cell debug)."""
    channel = str(stream_channel or "main").strip() or "main"
    if channel == "main":
        return str(tab_id)
    return f"{tab_id}:{channel}"


def begin_active_stream(active_key: str, session_id: str, url: str):
    """Register an in-flight chat before slow prep work so STOP can cancel early."""
    with _ACTIVE_STREAMS_LOCK:
        prev = _ACTIVE_STREAMS.get(active_key)
        if prev:
            _stop_active_stream(prev)
        _ACTIVE_STREAMS[active_key] = {
            "thread": None,
            "sessionId": session_id,
            "stopped": False,
            "url": url,
            "stream": None,
        }


def is_stream_stopped(active_key: str, session_id: str | None = None) -> bool:
    with _ACTIVE_STREAMS_LOCK:
        state = _ACTIVE_STREAMS.get(active_key)
        if not state:
            return False
        if session_id is not None and str(state.get("sessionId") or "") != str(session_id):
            return False
        return bool(state.get("stopped"))


def clear_active_stream(active_key: str, session_id: str):
    with _ACTIVE_STREAMS_LOCK:
        current = _ACTIVE_STREAMS.get(active_key)
        if current and str(current.get("sessionId") or "") == str(session_id):
            _ACTIVE_STREAMS.pop(active_key, None)


def _emit_stream_delta(
    *,
    tab_id,
    snapshot_url: str,
    history_key: str,
    session_id: str,
    delta: str,
) -> None:
    if not delta:
        return
    send_msg({
        "type": "CHAT_STREAM",
        "delta": delta,
        "tabId": tab_id,
        "url": snapshot_url,
        "notebookKey": history_key,
        "sessionId": session_id,
    })


def send_stream_end(
    url,
    tab_id,
    session_id,
    *,
    response="",
    stopped=False,
    error=None,
    snapshot_url=None,
    token_usage=None,
):
    payload = {
        "type": "CHAT_STREAM_END",
        "response": response,
        "stopped": stopped,
        "tabId": tab_id,
        "url": snapshot_url or url,
        "notebookKey": url,
        "sessionId": session_id,
    }
    if error:
        payload["error"] = error
    if isinstance(token_usage, dict) and int(token_usage.get("total_tokens") or 0) > 0:
        payload["tokenUsage"] = {
            "promptTokens": int(token_usage.get("prompt_tokens") or 0),
            "completionTokens": int(token_usage.get("completion_tokens") or 0),
            "cachedTokens": int(token_usage.get("cached_tokens") or 0),
            "totalTokens": int(token_usage.get("total_tokens") or 0),
            "requests": int(token_usage.get("requests") or 0),
        }
    send_msg(payload)


def _signal_remote_stop(session_id: str):
    if not session_id:
        return
    with _ACTIVE_STREAMS_LOCK:
        for k, state in list(_ACTIVE_STREAMS.items()):
            if state.get("sessionId") == session_id:
                _stop_active_stream(state)


def _interruptible_sleep(seconds: float, cancel_check) -> bool:
    """Sleep in small slices; return True if cancelled."""
    end = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < end:
        if callable(cancel_check) and cancel_check():
            return True
        time.sleep(min(0.1, end - time.monotonic()))
    return bool(callable(cancel_check) and cancel_check())


def _format_llm_error(exc: Exception) -> str:
    err = str(exc)
    low = err.lower()
    if "429" in err or "resource_exhausted" in low or "quota" in low or "queue_exceeded" in low or "too_many_requests" in low:
        return (
            "LLM API rate limit hit (Cerebras free tier: 5 requests/minute). "
            "Wait ~12s between calls; the host enforces this automatically in the ReAct loop."
        )
    if "context_length" in low or "too long" in low:
        return "Prompt too large for the model. Set CONTEXT_PACK_MODE=intent or narrow notebook scope."
    return f"LLM request failed: {err}"


def _agentic_status_only(text: str) -> bool:
    return str(text or "").strip() in ("Working…", "Working...")


def _apply_agentic_failure_text(final_text: str, err: Exception) -> str:
    msg = _format_llm_error(err)
    base = str(final_text or "").strip()
    if _agentic_status_only(base):
        return msg
    return (base + "\n\n" + msg).strip() if base else msg


def _run_streaming_chat(url, prompt, tab_id, session_id, history, context, mode, explicit_mode=None, context_meta=None):
    full_text = ""
    context_meta = context_meta if isinstance(context_meta, dict) else {}
    history_key = str(context_meta.get("history_key") or url)
    snapshot_url = str(context_meta.get("snapshot_url") or url)
    active_key = str(context_meta.get("active_key") or tab_id)
    memory_session_id = str(context_meta.get("cache_session_id") or session_id)
    attempt_id = ""
    state = None
    response = None

    def _is_stopped() -> bool:
        with _ACTIVE_STREAMS_LOCK:
            current = _ACTIVE_STREAMS.get(active_key)
            return bool(current and current.get("stopped"))

    if _LLM_CLIENT is None:
        if str(LLM_PROVIDER or "").lower() == "google":
            err = "Missing GEMINI_API_KEY (set LLM_PROVIDER=google and GEMINI_API_KEY in .env)."
        else:
            err = "Missing CEREBRAS_API_KEY environment variable."
        log(err)
        send_msg({"type": "CHAT_RESPONSE", "error": err, "tabId": tab_id, "url": snapshot_url, "notebookKey": history_key, "sessionId": session_id})
        send_msg({"type": "CHAT_STREAM_END", "error": err, "stopped": False, "tabId": tab_id, "url": snapshot_url, "notebookKey": history_key, "sessionId": session_id})
        return

    try:
        try:
            from .prompt_engineering import detect_mode, build_chat_messages, normalize_mode
        except Exception:
            from prompt_engineering import detect_mode, build_chat_messages, normalize_mode

        resolved_mode = detect_mode(
            prompt,
            explicit_mode or mode,
            has_cell_context=context_meta.get("cell_index") is not None,
        )
        resolved_mode = normalize_mode(resolved_mode)
        try:
            from .prompt_engineering import agentic_runtime_enabled
        except Exception:
            from prompt_engineering import agentic_runtime_enabled
        agentic_active = agentic_runtime_enabled(resolved_mode)
        log(
            f"AI Stream Request for {history_key} (session={session_id}, model={LLM_MODEL}, "
            f"mode={resolved_mode}, agentic={agentic_active})"
        )

        include_tools = agentic_active
        static_cache = bool(context_meta.get("static_cache"))
        turn_tail = str(context_meta.get("turn_tail") or "")
        if agentic_active:
            try:
                from .agentic_action_guard import is_actionable_notebook_request
            except Exception:
                from agentic_action_guard import is_actionable_notebook_request
            if is_actionable_notebook_request(prompt):
                action_tail = (
                    "Respond with ONE assistant message containing ALL tool_calls required "
                    "(every insert, edit, run_cell, etc.). Do not use one tool per round."
                )
                turn_tail = f"{action_tail}\n\n{turn_tail}".strip() if turn_tail else action_tail
        turn_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
        }
        messages = build_chat_messages(
            mode=resolved_mode,
            user_prompt=prompt,
            history=history,
            context=context,
            notebook_url=snapshot_url,
            include_tools=include_tools,
            turn_tail=turn_tail,
            static_cache=static_cache,
        )

        try:
            from .notebook_context import TOOL_FIRST_MODES
        except Exception:
            from notebook_context import TOOL_FIRST_MODES

        if _is_stopped():
            send_stream_end(history_key, tab_id, session_id, stopped=True, snapshot_url=snapshot_url)
            return

        coverage = str(context_meta.get("coverage") or "none")
        cell_idx = context_meta.get("cell_index")
        pre_stream_tools_done = False
        if resolved_mode in TOOL_FIRST_MODES:
            if _is_stopped():
                send_stream_end(history_key, tab_id, session_id, stopped=True, snapshot_url=snapshot_url)
                return
            try:
                from .tool_registry import registry as _registry_factory
                from .notebook_query import prefetch_notebook_queries
            except Exception:
                from tool_registry import registry as _registry_factory
                from notebook_query import prefetch_notebook_queries

            reg = _registry_factory()
            try:
                cell_idx_int = int(cell_idx) if cell_idx is not None else None
            except (TypeError, ValueError):
                cell_idx_int = None

            query_block, query_results = prefetch_notebook_queries(
                registry=reg,
                mode=resolved_mode,
                prompt=prompt,
                url=snapshot_url,
                cell_index=cell_idx_int,
                static_cache=static_cache,
                agentic=agentic_active,
            )
            if query_block:
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "user":
                        prev = str(messages[i].get("content") or "").strip()
                        messages[i]["content"] = (
                            f"{query_block}\n\n---\n\n{prev}" if prev else query_block
                        )
                        break
            ok_count = sum(1 for r in query_results if r.ok)
            log(
                f"Notebook query prefetch: mode={resolved_mode} "
                f"tools={len(query_results)} ok={ok_count} static_cache={static_cache}"
            )
            pre_stream_tools_done = bool(query_results)

        try:
            from .context_budget import fit_messages_to_budget, estimate_messages_tokens
        except Exception:
            from context_budget import fit_messages_to_budget, estimate_messages_tokens
        messages = fit_messages_to_budget(messages)
        est_tokens = estimate_messages_tokens(messages)
        log(f"Prompt budget: ~{est_tokens} est. tokens, {len(messages)} messages")
        ok_budget, budget_err = _preflight_prompt_budget(est_tokens)
        if not ok_budget:
            raise Exception(budget_err)

        cache_session_id = str(context_meta.get("cache_session_id") or session_id)
        completion_extra = _completion_extra_kwargs(session_id=cache_session_id, mode=resolved_mode)

        full_text = ""
        stream = None
        attempt_id = ""
        was_stopped = False
        stream_usage = None

        if agentic_active:
            if _is_stopped():
                send_stream_end(history_key, tab_id, session_id, stopped=True, snapshot_url=snapshot_url)
                return
            _emit_stream_delta(
                tab_id=tab_id,
                snapshot_url=snapshot_url,
                history_key=history_key,
                session_id=session_id,
                delta="Working…\n",
            )
            full_text = "Working…\n"
            log("Agentic mode: tool-first path (skipped prose stream)")
        else:
            while True:
                if _is_stopped():
                    break

                attempt_id = _begin_llm_request(cancel_check=_is_stopped)
                if not attempt_id:
                    break

                if _is_stopped():
                    break

                allowed, details = _check_token_limits()
                if not allowed:
                    raise Exception(f"Local rate limit hit: {details}")
                try:
                    log(f"Calling {_llm_api_label()} API (streaming)...")
                    t_api = time.monotonic()
                    stream = _LLM_CLIENT.chat.completions.create(
                        messages=messages,
                        model=LLM_MODEL,
                        stream=True,
                        temperature=TEMPERATURE,
                        top_p=TOP_P,
                        **completion_extra,
                    )
                    log(f"{_llm_api_label()} stream opened in {time.monotonic() - t_api:.2f}s")
                    break
                except Exception as stream_error:
                    err = str(stream_error)
                    if attempt_id:
                        _finalize_request_attempt(attempt_id, 0)
                        attempt_id = ""
                    if _is_stopped():
                        break
                    if "too_many_tokens" in err or "token_quota_exceeded" in err:
                        raise Exception(
                            "API token-per-minute limit exceeded. The full notebook prompt is too large. "
                            "Wait ~60 seconds, then retry. Or set CONTEXT_PACK_MODE=intent in .env."
                        ) from stream_error
                    if "429" in err or "queue_exceeded" in err or "too_many_requests_error" in err:
                        log("Queue busy. Retrying after rate-limit slot...")
                        if _interruptible_sleep(2.5, _is_stopped):
                            break
                        continue
                    raise

            if _is_stopped():
                final_text = ""
                if attempt_id:
                    _finalize_request_attempt(attempt_id, 0)
                send_msg({
                    "type": "CHAT_STREAM_END",
                    "response": "",
                    "stopped": True,
                    "tabId": tab_id,
                    "url": snapshot_url,
                    "notebookKey": history_key,
                    "sessionId": session_id,
                })
                return

            with _ACTIVE_STREAMS_LOCK:
                state = _ACTIVE_STREAMS.get(active_key)
                if state is not None:
                    state["stream"] = stream

            if _is_stopped():
                try:
                    _close_stream_handle(stream)
                except Exception:
                    pass
                if attempt_id:
                    _finalize_request_attempt(attempt_id, 0)
                send_msg({
                    "type": "CHAT_STREAM_END",
                    "response": "",
                    "stopped": True,
                    "tabId": tab_id,
                    "url": snapshot_url,
                    "notebookKey": history_key,
                    "sessionId": session_id,
                })
                return

            first_chunk_logged = False
            stream_usage = None
            try:
                from .token_usage import extract_usage_from_response
            except Exception:
                from token_usage import extract_usage_from_response
            for event in stream:
                with _ACTIVE_STREAMS_LOCK:
                    state = _ACTIVE_STREAMS.get(active_key)
                if not state or state.get("stopped"):
                    break

                usage_evt = extract_usage_from_response(event)
                if int(usage_evt.get("total_tokens") or 0) > 0:
                    stream_usage = usage_evt

                chunk = _chunk_text_from_event(event)
                if not chunk:
                    continue

                if not first_chunk_logged:
                    log("First stream chunk received")
                    first_chunk_logged = True

                full_text += chunk
                _emit_stream_delta(
                    tab_id=tab_id,
                    snapshot_url=snapshot_url,
                    history_key=history_key,
                    session_id=session_id,
                    delta=chunk,
                )

        with _ACTIVE_STREAMS_LOCK:
            state = _ACTIVE_STREAMS.get(active_key) or {}
            was_stopped = bool(state.get("stopped"))

        if was_stopped:
            if attempt_id:
                _finalize_request_attempt(attempt_id, 0)
            send_stream_end(
                history_key,
                tab_id,
                session_id,
                response=full_text.strip(),
                stopped=True,
                snapshot_url=snapshot_url,
            )
            return

        if not was_stopped and not full_text.strip():
            messages = fit_messages_to_budget(messages)
            fallback_id = _begin_llm_request(_is_stopped)
            if fallback_id:
                try:
                    response = _LLM_CLIENT.chat.completions.create(
                        messages=messages,
                        model=LLM_MODEL,
                        temperature=TEMPERATURE,
                        top_p=TOP_P,
                        **completion_extra,
                    )
                    full_text = _final_text_from_response(response)
                    est_fb = len(str(prompt or "").split()) + len(str(full_text or "").split())
                    try:
                        from .token_usage import extract_usage_from_response
                    except Exception:
                        from token_usage import extract_usage_from_response
                    _record_llm_usage(
                        attempt_id=fallback_id,
                        usage=extract_usage_from_response(response),
                        estimated_tokens=est_fb,
                        session_id=memory_session_id,
                        history_key=history_key,
                        mode=resolved_mode,
                        label="fallback",
                        turn_usage=turn_usage,
                    )
                except Exception as e:
                    _finalize_request_attempt(fallback_id, 0)
                    log(f"Fallback model call failed: {e}")

        final_text = full_text.strip()
        stream_est = estimate_messages_tokens(messages) + len(final_text.split())
        if attempt_id:
            _record_llm_usage(
                attempt_id=attempt_id,
                usage=stream_usage if not agentic_active else None,
                estimated_tokens=stream_est,
                session_id=memory_session_id,
                history_key=history_key,
                mode=resolved_mode,
                label="stream" if not agentic_active else "agentic_skip_stream",
                turn_usage=turn_usage,
            )

        if final_text and not was_stopped:
            memory_store.append(history_key, "assistant", final_text, session_id=memory_session_id)

        skip_post_stream_tools = not agentic_active
        agentic_tools_executed = 0
        agentic_had_batch = False

        # ReAct tool loop: Agentic mode only. Ask/Code use one prose stream + optional prefetch on the current turn.
        try:
            from .tool_registry import registry as _registry_factory, build_cerebras_tools
            from .agentic_mode import browser_tool_allowed

            tools = build_cerebras_tools(include_browser=agentic_active)
            max_tool_rounds = LLM_REACT_MAX_ROUNDS if agentic_active else 0
            parallel_tools = _parallel_tool_calls_flag(agentic=agentic_active) if agentic_active else False
            if tools and not was_stopped and not skip_post_stream_tools:
                tool_messages = list(messages)
                if final_text:
                    tool_messages.append({"role": "assistant", "content": final_text})

                direct_edit_done = False
                if agentic_active:
                    try:
                        from .agentic_tool_chain import build_direct_edit_from_prompt
                    except Exception:
                        from agentic_tool_chain import build_direct_edit_from_prompt
                    direct_args = build_direct_edit_from_prompt(
                        prompt,
                        url=snapshot_url,
                        tab_id=tab_id if isinstance(tab_id, int) else None,
                    )
                    if direct_args:
                        log(
                            "Direct edit_cell_by_index from prompt: "
                            f"cell {direct_args.get('cell_index')}"
                        )
                        reg_direct = _registry_factory()
                        direct_result = reg_direct.call("edit_cell_by_index", direct_args)
                        if isinstance(direct_result, dict) and direct_result.get("ok"):
                            direct_edit_done = True
                            cell_n = direct_args.get("cell_index")
                            final_text = (
                                f"Updated cell {cell_n} with the requested code."
                            )
                            _emit_stream_delta(
                                tab_id=tab_id,
                                snapshot_url=snapshot_url,
                                history_key=history_key,
                                session_id=session_id,
                                delta=f"\nUpdated cell {cell_n}.\n",
                            )
                            memory_store.append(
                                history_key, "assistant", final_text, session_id=memory_session_id
                            )
                        elif isinstance(direct_result, dict):
                            err_msg = (
                                f"Direct edit_cell_by_index failed: {direct_result.get('error')}"
                            )
                            log(err_msg)
                            final_text = (final_text + "\n\n" + err_msg).strip()

                if not direct_edit_done:
                    pipeline_state: dict | None = None
                    last_tool_queue_complete = False
                    for _round in range(max_tool_rounds):
                        if _is_stopped():
                            break
                        round_attempt = _begin_llm_request(_is_stopped)
                        if not round_attempt:
                            break
                        try:
                            tool_messages = fit_messages_to_budget(tool_messages)
                            create_kwargs: dict = {
                                "messages": tool_messages,
                                "model": LLM_MODEL,
                                "tools": tools,
                                "parallel_tool_calls": parallel_tools,
                                "temperature": TEMPERATURE,
                                "top_p": TOP_P,
                                **completion_extra,
                            }
                            if agentic_active and _round == 0:
                                try:
                                    from .agentic_action_guard import is_actionable_notebook_request
                                except Exception:
                                    from agentic_action_guard import is_actionable_notebook_request
                                if is_actionable_notebook_request(prompt):
                                    create_kwargs["tool_choice"] = "required"
                            tool_resp = _LLM_CLIENT.chat.completions.create(**create_kwargs)
                            round_est = len(json.dumps(tool_messages, ensure_ascii=False)) // 4
                            try:
                                from .token_usage import extract_usage_from_response
                            except Exception:
                                from token_usage import extract_usage_from_response
                            _record_llm_usage(
                                attempt_id=round_attempt,
                                usage=extract_usage_from_response(tool_resp),
                                estimated_tokens=round_est,
                                session_id=memory_session_id,
                                history_key=history_key,
                                mode=resolved_mode,
                                label=f"tool_round_{_round}",
                                turn_usage=turn_usage,
                            )
                        except Exception as e:
                            _finalize_request_attempt(round_attempt, 0)
                            log(f"Tool-enabled model call failed: {e}")
                            if agentic_active:
                                final_text = _apply_agentic_failure_text(final_text, e)
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta=("\n\n" if full_text else "") + _format_llm_error(e),
                                )
                            break

                        dumped = tool_resp.model_dump() if hasattr(tool_resp, "model_dump") else {}
                        choice = (dumped.get("choices") or [{}])[0]
                        assistant_msg = choice.get("message") or {}
                        tool_calls = assistant_msg.get("tool_calls") or []

                        if not tool_calls:
                            followup = _final_text_from_response(tool_resp).strip()
                            try:
                                from .agentic_action_guard import (
                                    agentic_must_continue_with_tools,
                                    build_action_nudge,
                                )
                            except Exception:
                                from agentic_action_guard import (
                                    agentic_must_continue_with_tools,
                                    build_action_nudge,
                                )

                            pipeline_active = bool(
                                pipeline_state
                                and pipeline_state.get("active")
                                and not pipeline_state.get("complete")
                            )
                            run_queue_done = last_tool_queue_complete
                            must_act = agentic_must_continue_with_tools(
                                prompt=prompt,
                                followup_text=followup,
                                tools_executed=agentic_tools_executed,
                                pipeline_active=pipeline_active and not run_queue_done,
                            )

                            if agentic_active and must_act and _round < max_tool_rounds - 1:
                                if followup:
                                    tool_messages.append(
                                        {"role": "assistant", "content": followup}
                                    )
                                tool_messages.append({
                                    "role": "user",
                                    "content": build_action_nudge(
                                        prompt,
                                        tools_executed=agentic_tools_executed,
                                        round_idx=_round,
                                    ),
                                })
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta="\nAction required — calling tools…\n",
                                )
                                if pipeline_active:
                                    pending = pipeline_state.get("pending_runs") or []
                                    next_ci = pending[0] if pending else None
                                    last_ci = pipeline_state.get("last_run_cell")
                                    tool_messages.append({
                                        "role": "user",
                                        "content": (
                                            f"Pipeline pending_runs={pending}, "
                                            f"last_run_cell={last_ci}. "
                                            f"Emit run_cell({next_ci}) if prior output OK."
                                        ),
                                    })
                                continue

                            if (
                                agentic_active
                                and pipeline_state
                                and pipeline_state.get("active")
                                and not pipeline_state.get("complete")
                                and not agentic_had_batch
                            ):
                                pending = pipeline_state.get("pending_runs") or []
                                next_ci = pending[0] if pending else None
                                last_ci = pipeline_state.get("last_run_cell")
                                nudge = (
                                    "Pipeline still active — do not give manual instructions. "
                                    f"Last run cell: {last_ci}. Pending: {pending}. "
                                )
                                if pipeline_state.get("last_run_ok") is False:
                                    nudge += f"Fix cell {last_ci} with edit_cell_by_index + run_cell, then continue."
                                elif next_ci is not None:
                                    nudge += (
                                        f"In one tool turn: verify pipeline_step from the last "
                                        f"workflow_verification, then emit run_cell({next_ci})."
                                    )
                                tool_messages.append({"role": "user", "content": nudge})
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta="\nContinuing pipeline…\n",
                                )
                                continue
                            if followup:
                                final_text = followup
                            break

                        agentic_tools_executed += len(tool_calls)

                        tool_messages.append({
                            "role": "assistant",
                            "content": assistant_msg.get("content") or "",
                            "tool_calls": tool_calls,
                        })

                        reg = _registry_factory()
                        if agentic_active:
                            try:
                                from .agentic_batch_executor import execute_agentic_batch, should_use_batch_executor
                                from .agentic_mode import browser_tool_allowed as _batch_browser_allowed
                            except Exception:
                                from agentic_batch_executor import execute_agentic_batch, should_use_batch_executor
                                from agentic_mode import browser_tool_allowed as _batch_browser_allowed

                        if agentic_active and should_use_batch_executor(tool_calls, agentic_active=True):
                            try:
                                from .agentic_batch_executor import workflow_needs_llm_followup
                            except Exception:
                                from agentic_batch_executor import workflow_needs_llm_followup

                            verification = execute_agentic_batch(
                                tool_calls,
                                user_prompt=prompt,
                                url=snapshot_url,
                                tab_id=tab_id if isinstance(tab_id, int) else None,
                                registry=reg,
                                browser_tool_allowed=_batch_browser_allowed,
                                mode=resolved_mode,
                                pipeline_state=pipeline_state,
                            )
                            pipeline_state = verification.get("pipeline") or pipeline_state
                            if verification.get("tool_queue_complete") or verification.get("run_queue_complete"):
                                last_tool_queue_complete = True
                            agentic_had_batch = True
                            batch_payload = _compact_tool_result_content(
                                json.dumps(verification, ensure_ascii=False)
                            )
                            for tc_idx, tc in enumerate(tool_calls):
                                batch_tool_id = tc.get("id") if isinstance(tc, dict) else None
                                if not batch_tool_id:
                                    batch_tool_id = f"host_workflow_verification_{_round}_{tc_idx}"
                                tool_messages.append({
                                    "role": "tool",
                                    "tool_call_id": batch_tool_id,
                                    "content": batch_payload,
                                })

                            if verification.get("verified"):
                                cell_n = verification.get("cell_index")
                                pipeline = verification.get("pipeline") or {}
                                if pipeline.get("active") and not pipeline.get("complete"):
                                    pending = pipeline.get("pending_runs") or []
                                    status_line = (
                                        f"\nPipeline step complete (cell {cell_n}). "
                                        f"Pending runs: {pending}\n"
                                    )
                                elif verification.get("tool_queue_complete") or verification.get("run_queue_complete"):
                                    n = len(verification.get("runs_executed") or [])
                                    status_line = f"\nAll {n} cells executed — summarizing…\n"
                                elif verification.get("run_completed"):
                                    status_line = (
                                        f"\nRun complete (cell {cell_n}) — reviewing output…\n"
                                        if cell_n
                                        else "\nRun complete — reviewing output…\n"
                                    )
                                else:
                                    status_line = (
                                        f"\nWorkflow verified"
                                        + (f" (cell {cell_n})." if cell_n else ".")
                                        + "\n"
                                    )
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta=status_line,
                                )
                            else:
                                exec_err = verification.get("execution_error") or {}
                                if exec_err:
                                    cell_n = exec_err.get("cell_index") or verification.get("cell_index")
                                    summary = exec_err.get("error_summary") or "execution error in cell output"
                                    pending = verification.get("pending_run_cells") or []
                                    log(f"Batch run error cell {cell_n}: {summary}")
                                    _emit_stream_delta(
                                        tab_id=tab_id,
                                        snapshot_url=snapshot_url,
                                        history_key=history_key,
                                        session_id=session_id,
                                        delta=(
                                            f"\nRun error in cell {cell_n}: {summary}\n"
                                            + (f"Queue stopped — pending: {pending}\n" if pending else "")
                                            + "Analyzing output to fix…\n"
                                        ),
                                    )
                                else:
                                    err_msg = "Workflow verification failed — see workflow_verification JSON."
                                    log(err_msg)
                                    final_text = (final_text + "\n\n" + err_msg).strip()

                            if workflow_needs_llm_followup(verification):
                                continue
                            break

                        for tc_idx, tc in enumerate(tool_calls):
                            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                            fname = fn.get("name")
                            raw_args = fn.get("arguments") or "{}"
                            try:
                                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                            except Exception:
                                parsed_args = {}
                            parsed_args.setdefault("url", snapshot_url)
                            if isinstance(tab_id, int) and tab_id > 0:
                                parsed_args.setdefault("tab_id", tab_id)

                            try:
                                allowed, block_err = browser_tool_allowed(resolved_mode, str(fname or ""))
                                if not allowed:
                                    result = {"ok": False, "error": block_err, "tool": fname}
                                else:
                                    result = reg.call(fname, parsed_args)
                            except Exception as exc:
                                result = {"ok": False, "error": str(exc)}

                            tool_call_id = tc.get("id") if isinstance(tc, dict) else None
                            if not tool_call_id:
                                tool_call_id = f"call_{fname or 'tool'}_{_round}_{tc_idx}"

                            tool_messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": _compact_tool_result_content(
                                    json.dumps(result, ensure_ascii=False)
                                ),
                            })

                            if isinstance(result, dict) and result.get("ok") is False:
                                err_msg = f"Tool '{fname}' failed: {result.get('error')}"
                                log(err_msg)
                                final_text = (final_text + "\n\n" + err_msg).strip()

                            if agentic_active and fname == "insert_cell":
                                try:
                                    from .agentic_tool_chain import build_edit_after_insert
                                except Exception:
                                    from agentic_tool_chain import build_edit_after_insert
                                chain_args = build_edit_after_insert(
                                    prompt,
                                    parsed_args,
                                    result if isinstance(result, dict) else {},
                                    url=snapshot_url,
                                    tab_id=tab_id if isinstance(tab_id, int) else None,
                                )
                                if chain_args:
                                    log(
                                        f"Auto-chaining edit_cell_by_index on cell "
                                        f"{chain_args.get('cell_index')} after insert_cell"
                                    )
                                    try:
                                        edit_result = reg.call("edit_cell_by_index", chain_args)
                                    except Exception as exc:
                                        edit_result = {"ok": False, "error": str(exc), "tool": "edit_cell_by_index"}
                                    tool_messages.append({
                                        "role": "tool",
                                        "tool_call_id": f"host_chain_edit_{_round}",
                                        "content": _compact_tool_result_content(
                                            json.dumps(
                                                {"auto_chained": True, **edit_result},
                                                ensure_ascii=False,
                                            )
                                        ),
                                    })
                                    if isinstance(edit_result, dict) and edit_result.get("ok"):
                                        _emit_stream_delta(
                                            tab_id=tab_id,
                                            snapshot_url=snapshot_url,
                                            history_key=history_key,
                                            session_id=session_id,
                                            delta=(
                                                f"\nInserted cell {chain_args.get('cell_index')} "
                                                f"and set content.\n"
                                            ),
                                        )
                                    elif isinstance(edit_result, dict) and edit_result.get("ok") is False:
                                        err_msg = (
                                            f"Auto edit_cell_by_index failed: {edit_result.get('error')}"
                                        )
                                        log(err_msg)
                                        final_text = (final_text + "\n\n" + err_msg).strip()

                    if not _is_stopped():
                        final_attempt = _begin_llm_request(_is_stopped)
                        if final_attempt:
                            try:
                                tool_messages = fit_messages_to_budget(tool_messages)
                                final_resp = _LLM_CLIENT.chat.completions.create(
                                    messages=tool_messages,
                                    model=LLM_MODEL,
                                    temperature=TEMPERATURE,
                                    top_p=TOP_P,
                                    **completion_extra,
                                )
                                followup = _final_text_from_response(final_resp).strip()
                                final_est = len(json.dumps(tool_messages, ensure_ascii=False)) // 4
                                try:
                                    from .token_usage import extract_usage_from_response
                                except Exception:
                                    from token_usage import extract_usage_from_response
                                _record_llm_usage(
                                    attempt_id=final_attempt,
                                    usage=extract_usage_from_response(final_resp),
                                    estimated_tokens=final_est,
                                    session_id=memory_session_id,
                                    history_key=history_key,
                                    mode=resolved_mode,
                                    label="tool_final",
                                    turn_usage=turn_usage,
                                )
                                if followup:
                                    final_text = followup
                                    if agentic_active:
                                        _emit_stream_delta(
                                            tab_id=tab_id,
                                            snapshot_url=snapshot_url,
                                            history_key=history_key,
                                            session_id=session_id,
                                            delta=("\n" if full_text else "") + followup,
                                        )
                                    memory_store.append(history_key, "assistant", final_text, session_id=memory_session_id)
                            except Exception as e:
                                _finalize_request_attempt(final_attempt, 0)
                                log(f"Final model call after tools failed: {e}")
                                if agentic_active:
                                    final_text = _apply_agentic_failure_text(final_text, e)
                                    _emit_stream_delta(
                                        tab_id=tab_id,
                                        snapshot_url=snapshot_url,
                                        history_key=history_key,
                                        session_id=session_id,
                                        delta=("\n\n" if full_text else "") + _format_llm_error(e),
                                    )
        except Exception as e:
            log(f"Orchestration error: {e}")

        if agentic_active and not final_text.strip():
            final_text = (
                "Agentic request finished without a response. "
                "Restart the host after code updates and check host.log for API/tool errors."
            )
        elif agentic_active and _agentic_status_only(final_text):
            final_text = (
                "Tools ran but no summary was returned. "
                "Check host.log or retry after the Gemini quota resets."
            )
        elif agentic_active and not was_stopped:
            try:
                from .agentic_action_guard import (
                    is_actionable_notebook_request,
                    looks_like_instruction_only_response,
                )
            except Exception:
                from agentic_action_guard import (
                    is_actionable_notebook_request,
                    looks_like_instruction_only_response,
                )
            no_tools = agentic_tools_executed == 0 and not agentic_had_batch
            if (
                no_tools
                and is_actionable_notebook_request(prompt)
                and looks_like_instruction_only_response(final_text)
            ):
                final_text = (
                    "Agentic mode requires tool execution, but the model returned "
                    "manual instructions instead of tool calls. Retry the request."
                )

        try:
            from .token_usage import read_usage_totals
        except Exception:
            from token_usage import read_usage_totals
        day_totals = read_usage_totals(hours=24)
        if int(turn_usage.get("total_tokens") or 0) > 0:
            log(
                f"Turn tokens: {turn_usage.get('total_tokens')} "
                f"(cached {turn_usage.get('cached_tokens', 0)}); "
                f"24h total: {day_totals.get('total_tokens', 0)}"
            )

        send_stream_end(
            history_key,
            tab_id,
            session_id,
            response=final_text,
            stopped=was_stopped,
            snapshot_url=snapshot_url,
            token_usage=turn_usage,
        )

    except Exception as e:
        err_text = f"Error: {e}"
        log(f"AI Stream Error: {e}")
        if attempt_id:
            _finalize_request_attempt(attempt_id, 0)
        if not _is_stopped():
            memory_store.append(history_key, "assistant", err_text, session_id=memory_session_id)
        send_msg({"type": "CHAT_RESPONSE", "error": str(e), "tabId": tab_id, "url": snapshot_url, "notebookKey": history_key, "sessionId": session_id})
        send_msg({"type": "CHAT_STREAM_END", "error": str(e), "stopped": False, "tabId": tab_id, "url": snapshot_url, "notebookKey": history_key, "sessionId": session_id})
    finally:
        clear_active_stream(active_key, session_id)
