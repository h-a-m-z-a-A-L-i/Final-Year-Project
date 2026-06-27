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
        CEREBRAS_SECONDARY_API_KEY,
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
            CEREBRAS_SECONDARY_API_KEY,
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
    key_profile = ""
    try:
        if _LLM_CLIENT is not None and hasattr(_LLM_CLIENT, "active_profile"):
            key_profile = str(_LLM_CLIENT.active_profile or "")
    except Exception:
        pass
    record_token_event(
        attempt_id=attempt_id,
        session_id=session_id,
        history_key=history_key,
        mode=mode,
        label=label,
        usage=parsed or None,
        estimated_tokens=estimated_tokens,
        key_profile=key_profile,
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


def _completion_extra_kwargs(
    *,
    session_id: str | None = None,
    mode: str | None = None,
    messages: list | None = None,
) -> dict:
    """Provider-specific API options (Gemini context cache / Cerebras reasoning + prompt cache)."""
    try:
        from .config import LLM_PROVIDER
    except Exception:
        from config import LLM_PROVIDER

    provider = str(LLM_PROVIDER or "").lower()
    if provider == "google":
        try:
            from .llm_provider import gemini_completion_extras
            from .config import LLM_MODEL
        except Exception:
            from llm_provider import gemini_completion_extras
            from config import LLM_MODEL
        return gemini_completion_extras(
            messages=messages,
            model=LLM_MODEL,
            session_id=session_id,
            mode=mode,
        )

    if provider != "cerebras":
        return {}

    try:
        from .llm_provider import cerebras_completion_extras
    except Exception:
        from llm_provider import cerebras_completion_extras
    return cerebras_completion_extras(session_id=session_id, mode=mode)


def _augment_llm_create_kwargs(kwargs: dict, messages: list | None = None) -> dict:
    """Merge provider-specific create() kwargs (e.g. Gemini max_tokens + cached_content)."""
    try:
        from .config import LLM_PROVIDER
    except Exception:
        from config import LLM_PROVIDER
    if str(LLM_PROVIDER or "").lower() != "google":
        return kwargs
    out = dict(kwargs)
    try:
        from .llm_provider import gemini_completion_extras
        from .config import LLM_MODEL
    except Exception:
        from llm_provider import gemini_completion_extras
        from config import LLM_MODEL
    extra = gemini_completion_extras(messages=messages, model=LLM_MODEL)
    if extra.get("max_tokens") is not None:
        out["max_tokens"] = extra["max_tokens"]
    eb = dict(out.get("extra_body") or {})
    if isinstance(extra.get("extra_body"), dict):
        eb.update(extra["extra_body"])
    if eb:
        out["extra_body"] = eb
    return out


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


def filter_tool_calls_for_mode(tool_calls: list | None, mode: str) -> list:
    """Drop LLM tool_calls outside Agentic mode (Ask/Code are prose-only on the host)."""
    if not tool_calls:
        return []
    try:
        from .prompt_engineering import agentic_runtime_enabled
    except Exception:
        from prompt_engineering import agentic_runtime_enabled
    if agentic_runtime_enabled(mode):
        return list(tool_calls)
    return []


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
    active_key: str | None = None,
) -> None:
    if not delta:
        return
    if active_key and is_stream_stopped(str(active_key), session_id):
        return
    if not active_key and session_id:
        with _ACTIVE_STREAMS_LOCK:
            for state in _ACTIVE_STREAMS.values():
                if str(state.get("sessionId") or "") == str(session_id) and state.get("stopped"):
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


def _signal_remote_stop(session_id: str, *, active_key: str | None = None):
    with _ACTIVE_STREAMS_LOCK:
        if active_key and active_key in _ACTIVE_STREAMS:
            _stop_active_stream(_ACTIVE_STREAMS.get(active_key))
        elif session_id:
            for state in _ACTIVE_STREAMS.values():
                if str(state.get("sessionId") or "") == str(session_id):
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
    try:
        from .config import LLM_PROVIDER, RPM_LIMIT
    except Exception:
        from config import LLM_PROVIDER, RPM_LIMIT
    if "429" in err or "resource_exhausted" in low or "quota" in low or "queue_exceeded" in low or "too_many_requests" in low:
        if str(LLM_PROVIDER or "").lower() == "google":
            return (
                f"LLM API rate limit hit (Gemini free tier: {RPM_LIMIT} requests/minute). "
                "Wait ~4s between calls; the host enforces this automatically in the ReAct loop."
            )
        return (
            "LLM API rate limit hit (Cerebras free tier: 5 requests/minute). "
            "Wait ~12s between calls; the host enforces this automatically in the ReAct loop."
        )
    if "context_length" in low or "too long" in low:
        return "Prompt too large for the model. Set CONTEXT_PACK_MODE=intent or narrow notebook scope."
    if "timed out" in low or "timeout" in low:
        return (
            "LLM request failed: Request timed out. "
            "The host retried automatically. Check network/API status and retry, "
            "or increase CEREBRAS_REQUEST_TIMEOUT in .env."
        )
    if "connection error" in low or "connection reset" in low or "connection refused" in low:
        return (
            "LLM request failed: Connection error. "
            "Check internet/VPN/firewall and Cerebras API status, then retry. "
            "The host retried with backoff automatically."
        )
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
        err = "Missing Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY in .env)."
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
        try:
            from .tool_call_terminal import trace_session_start
        except Exception:
            try:
                from tool_call_terminal import trace_session_start
            except Exception:
                trace_session_start = None  # type: ignore
        if trace_session_start:
            trace_session_start(
                mode=resolved_mode,
                session_id=memory_session_id,
                url=snapshot_url,
            )
        try:
            from .config import LLM_PROVIDER
            from .agentic_text_tools import text_tool_calling_enabled
        except Exception:
            from config import LLM_PROVIDER
            from agentic_text_tools import text_tool_calling_enabled
        use_text_tools = text_tool_calling_enabled(LLM_PROVIDER, agentic=agentic_active)
        log(
            f"AI Stream Request for {history_key} (session={session_id}, model={LLM_MODEL}, "
            f"mode={resolved_mode}, agentic={agentic_active}, text_tools={use_text_tools})"
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
                target_ci = context_meta.get("cell_index")
                try:
                    from .agentic_action_guard import count_implied_tool_actions
                except Exception:
                    from agentic_action_guard import count_implied_tool_actions
                implied_actions = count_implied_tool_actions(prompt)
                if use_text_tools:
                    if target_ci is not None:
                        action_tail = (
                            f"Target cell is {int(target_ci)} (from notebook context). "
                            f"Emit one <agent_tool_batch>: notebook_get_cell if needed, "
                            f"edit_cell_by_index cell_index={int(target_ci)}, then run_cell if user asked to run/fix."
                        )
                    elif implied_actions >= 2:
                        action_tail = (
                            f"This request needs {implied_actions} tool actions. "
                            f"Emit one <agent_tool_batch> with ALL {implied_actions}+ tools in one JSON array "
                            f"(every delete_by_index, insert_cell, edit_cell_by_index, run_cell required)."
                        )
                    else:
                        action_tail = (
                            "Emit one <agent_tool_batch> with all tools for this task "
                            "(reads, edit_cell_by_index, run_cell)."
                        )
                else:
                    if target_ci is not None:
                        action_tail = (
                            f"Target cell is {int(target_ci)} (from notebook context). "
                            f"Respond with ONE assistant message containing ALL native tool_calls: "
                            f"notebook_get_cell if needed, edit_cell_by_index cell_index={int(target_ci)}, "
                            f"then run_cell if the user asked to run/fix."
                        )
                    elif implied_actions >= 2:
                        action_tail = (
                            f"This request needs {implied_actions} tool actions. "
                            f"Respond with ONE assistant message containing ALL native tool_calls "
                            f"(parallel_tool_calls enabled): every delete_by_index, insert_cell, "
                            f"edit_cell_by_index, and run_cell required — not one tool per API round."
                        )
                    else:
                        action_tail = (
                            "Respond with ONE assistant message containing ALL native tool_calls "
                            "(parallel_tool_calls enabled): every read, insert_cell, edit_cell_by_index, "
                            "and run_cell required for this task."
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
            text_tool_calls=use_text_tools,
        )
        if agentic_active and messages:
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    messages[i]["_react_original_user"] = True
                    break

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
        completion_extra = _completion_extra_kwargs(
            session_id=cache_session_id,
            mode=resolved_mode,
            messages=messages,
        )

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
                active_key=active_key,
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
                        **_augment_llm_create_kwargs(
                            {
                                "messages": messages,
                                "model": LLM_MODEL,
                                "stream": True,
                                "temperature": TEMPERATURE,
                                "top_p": TOP_P,
                                **completion_extra,
                            },
                            messages=messages,
                        )
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
                        **_augment_llm_create_kwargs(
                            {
                                "messages": messages,
                                "model": LLM_MODEL,
                                "temperature": TEMPERATURE,
                                "top_p": TOP_P,
                                **completion_extra,
                            },
                            messages=messages,
                        )
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

        if final_text and not was_stopped and not agentic_active:
            memory_store.append(history_key, "assistant", final_text, session_id=memory_session_id)

        skip_post_stream_tools = not agentic_active
        agentic_tools_executed = 0
        agentic_had_batch = False
        last_batch_verification: dict | None = None
        _turn_final_assistant_text = ""
        try:
            from .execution_integrity import ExecutionIntegrityState
        except Exception:
            from execution_integrity import ExecutionIntegrityState
        integrity_state = ExecutionIntegrityState()
        llm_request_failed = False
        llm_failure_message = ""

        def _audit_tool_names(calls: list | None) -> list[str]:
            try:
                from .tool_execution_audit import _tool_names_from_calls
            except Exception:
                from tool_execution_audit import _tool_names_from_calls
            return _tool_names_from_calls(calls)

        def _emit_batch_audit(**kwargs) -> None:
            try:
                from .tool_execution_audit import build_batch_record, log_batch_audit
            except Exception:
                from tool_execution_audit import build_batch_record, log_batch_audit
            try:
                log_batch_audit(build_batch_record(**kwargs))
            except Exception as exc:
                log(f"Tool execution audit log failed: {exc}")

        def _assistant_claims_success(text: str) -> bool:
            try:
                from .tool_execution_audit import _assistant_claims_success as _claims
            except Exception:
                from tool_execution_audit import _assistant_claims_success as _claims
            return _claims(text)

        # ReAct tool loop: Agentic mode only. Ask/Code use one prose stream + optional prefetch on the current turn.
        try:
            from .tool_registry import registry as _registry_factory, build_cerebras_tools
            from .agentic_mode import browser_tool_allowed

            tools = (
                build_cerebras_tools(
                    include_browser=agentic_active,
                    strict=str(LLM_PROVIDER or "").lower() == "cerebras",
                )
                if not use_text_tools
                else []
            )
            if agentic_active and AGENTIC_FIRE_AND_FORGET:
                max_tool_rounds = AGENTIC_MAX_TOOL_ROUNDS
            else:
                max_tool_rounds = LLM_REACT_MAX_ROUNDS if agentic_active else 0
            parallel_tools = _parallel_tool_calls_flag(agentic=agentic_active) if agentic_active else False
            if (tools or use_text_tools) and not was_stopped and not skip_post_stream_tools and not _is_stopped():
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
                    queue_error_state: dict | None = None
                    error_recovery_attempts = 0
                    react_llm_calls = 0
                    try:
                        from .agentic_action_guard import (
                            MAX_ERROR_RECOVERY_ROUNDS,
                            MAX_INCOMPLETE_BATCH_NUDGES,
                        )
                    except Exception:
                        from agentic_action_guard import (
                            MAX_ERROR_RECOVERY_ROUNDS,
                            MAX_INCOMPLETE_BATCH_NUDGES,
                        )
                    try:
                        from .agent_state import empty_agent_state, inject_agent_state_message, update_agent_state_from_verification
                        from .agent_planner import (
                            apply_plan_from_llm_response,
                            build_plan_request_nudge,
                            build_step_execution_nudge,
                            load_agent_plan,
                            persist_agent_plan,
                            planning_phase_active,
                            planner_enabled,
                            update_plan_from_verification,
                        )
                    except Exception:
                        from agent_state import empty_agent_state, inject_agent_state_message, update_agent_state_from_verification
                        from agent_planner import (
                            apply_plan_from_llm_response,
                            build_plan_request_nudge,
                            build_step_execution_nudge,
                            load_agent_plan,
                            persist_agent_plan,
                            planning_phase_active,
                            planner_enabled,
                            update_plan_from_verification,
                        )
                    loaded_plan = load_agent_plan(history_key, goal=prompt) if planner_enabled() else None
                    agent_state = loaded_plan if loaded_plan else empty_agent_state(goal=prompt)
                    plan_generation_pending = planning_phase_active(agent_state, prompt=prompt)
                    plan_nudge_sent = False
                    prose_only_streak = 0
                    incomplete_batch_nudges = 0
                    last_parse_result = None
                    agentic_fire_and_forget_done = False
                    agentic_turn_resolved = False
                    _seq_executed: list[dict] = []
                    _ff_cumulative_executed: list[dict] = []
                    _ff_rounds_dispatched = 0
                    query_rounds_used = 0
                    consecutive_query_only_rounds = 0
                    phase0_nudge_sent = False
                    phase1_nudge_sent = False
                    for _round in range(max_tool_rounds):
                        if _is_stopped():
                            break
                        try:
                            from .tool_call_terminal import trace_react_round
                        except Exception:
                            try:
                                from tool_call_terminal import trace_react_round
                            except Exception:
                                trace_react_round = None  # type: ignore
                        if trace_react_round:
                            trace_react_round(_round)
                        round_attempt = _begin_llm_request(_is_stopped)
                        if not round_attempt:
                            break
                        try:
                            try:
                                from .context_budget import fit_react_messages_to_budget, _react_protected_indices, messages_for_api
                            except Exception:
                                from context_budget import fit_react_messages_to_budget, _react_protected_indices, messages_for_api
                            try:
                                from .react_message_debug import apply_fit_messages_to_budget_with_debug
                            except Exception:
                                from react_message_debug import apply_fit_messages_to_budget_with_debug
                            try:
                                from .agent_trace import trace_react_event
                            except Exception:
                                from agent_trace import trace_react_event

                            pre_fit = list(tool_messages)
                            if plan_generation_pending and not plan_nudge_sent:
                                tool_messages.append({
                                    "role": "user",
                                    "content": build_plan_request_nudge(prompt),
                                })
                                plan_nudge_sent = True
                            if (
                                agentic_active
                                and AGENTIC_FIRE_AND_FORGET
                                and AGENTIC_MANDATORY_TWO_PHASE
                            ):
                                try:
                                    from .agentic_action_guard import (
                                        build_phase0_query_nudge,
                                        build_phase1_implementation_nudge,
                                        is_actionable_notebook_request,
                                    )
                                except Exception:
                                    from agentic_action_guard import (
                                        build_phase0_query_nudge,
                                        build_phase1_implementation_nudge,
                                        is_actionable_notebook_request,
                                    )
                                if is_actionable_notebook_request(prompt):
                                    if _round == 0 and not phase0_nudge_sent:
                                        tool_messages.append({
                                            "role": "user",
                                            "content": build_phase0_query_nudge(prompt),
                                        })
                                        phase0_nudge_sent = True
                                    elif _round == 1 and not phase1_nudge_sent:
                                        tool_messages.append({
                                            "role": "user",
                                            "content": build_phase1_implementation_nudge(prompt),
                                        })
                                        phase1_nudge_sent = True
                            tool_messages = inject_agent_state_message(tool_messages, agent_state)
                            fitted, removed_fps = fit_react_messages_to_budget(
                                tool_messages,
                                original_user_prompt=prompt,
                            )
                            tool_messages = apply_fit_messages_to_budget_with_debug(
                                fitted,
                                round_idx=_round,
                                original_user_prompt=prompt,
                                pre_trim_messages=pre_fit,
                                removed_fps=removed_fps,
                            )
                            try:
                                from .context_budget import sanitize_tool_message_chain
                            except Exception:
                                from context_budget import sanitize_tool_message_chain
                            tool_messages = sanitize_tool_message_chain(tool_messages)
                            protected = _react_protected_indices(tool_messages, original_user_prompt=prompt)
                            trace_react_event(
                                event="pre_llm_call",
                                round_idx=_round,
                                messages=tool_messages,
                                protected_indices=protected,
                                removed_fingerprints=removed_fps,
                                agent_state=agent_state,
                            )
                            react_llm_calls += 1
                            create_kwargs: dict = _augment_llm_create_kwargs(
                                {
                                    "messages": messages_for_api(tool_messages),
                                    "model": LLM_MODEL,
                                    "temperature": TEMPERATURE,
                                    "top_p": TOP_P,
                                    **completion_extra,
                                },
                                messages=tool_messages,
                            )
                            if not use_text_tools:
                                create_kwargs["tools"] = tools
                                create_kwargs["parallel_tool_calls"] = parallel_tools
                                if agentic_active and _round == 0:
                                    try:
                                        from .agentic_action_guard import is_actionable_notebook_request
                                    except Exception:
                                        from agentic_action_guard import is_actionable_notebook_request
                                    if is_actionable_notebook_request(prompt):
                                        create_kwargs["tool_choice"] = "required"
                            tool_resp = _LLM_CLIENT.chat.completions.create(**create_kwargs)
                            if _is_stopped():
                                _finalize_request_attempt(round_attempt, 0)
                                break
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
                            llm_request_failed = True
                            llm_failure_message = _format_llm_error(e)
                            try:
                                from .tool_call_terminal import trace_llm_error
                            except Exception:
                                try:
                                    from tool_call_terminal import trace_llm_error
                                except Exception:
                                    trace_llm_error = None  # type: ignore
                            if trace_llm_error:
                                trace_llm_error(_round, llm_failure_message)
                            if agentic_active:
                                final_text = llm_failure_message
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta=("\n\n" if full_text else "") + llm_failure_message,
                                )
                            break

                        dumped = tool_resp.model_dump() if hasattr(tool_resp, "model_dump") else {}
                        choice = (dumped.get("choices") or [{}])[0]
                        assistant_msg = choice.get("message") or {}
                        tool_calls = filter_tool_calls_for_mode(
                            assistant_msg.get("tool_calls") or [], resolved_mode
                        )
                        if (assistant_msg.get("tool_calls") or []) and not tool_calls:
                            log(
                                f"Ignored {len(assistant_msg.get('tool_calls') or [])} tool_calls "
                                f"— mode={resolved_mode} (browser tools require Agentic)"
                            )

                        if not use_text_tools and tool_calls and agentic_active:
                            try:
                                from .agentic_text_tools import inject_tool_defaults
                            except Exception:
                                from agentic_text_tools import inject_tool_defaults
                            tool_calls = inject_tool_defaults(
                                tool_calls,
                                url=snapshot_url,
                                tab_id=tab_id if isinstance(tab_id, int) else None,
                            )
                            assistant_msg = dict(assistant_msg)
                            assistant_msg["tool_calls"] = tool_calls

                        if use_text_tools:
                            try:
                                from .agentic_text_tools import (
                                    inject_tool_defaults,
                                    parse_text_tool_batch_result,
                                    strip_tool_batch_from_text,
                                )
                                from .agentic_text_tools_types import build_unknown_tools_nudge
                            except Exception:
                                from agentic_text_tools import (
                                    inject_tool_defaults,
                                    parse_text_tool_batch_result,
                                    strip_tool_batch_from_text,
                                )
                                from agentic_text_tools_types import build_unknown_tools_nudge
                            raw_content = _final_text_from_response(tool_resp)
                            _action_required = is_actionable_notebook_request(prompt)
                            parse_result = parse_text_tool_batch_result(
                                raw_content,
                                action_required=_action_required,
                            )
                            last_parse_result = parse_result
                            tool_calls = parse_result.tool_calls
                            try:
                                from .agent_metrics import record_turn_metric
                            except Exception:
                                from agent_metrics import record_turn_metric
                            record_turn_metric(
                                event="text_tool_parse",
                                increment={"tool_batch_parse_attempts": 1},
                                extra=parse_result.to_feedback_dict(),
                            )
                            if parse_result.multiple_batches:
                                record_turn_metric(
                                    event="multiple_batch_merged",
                                    increment={"multiple_batch_merge_events": 1},
                                    extra={"batch_count": parse_result.batch_count},
                                )
                            if parse_result.unknown_tools:
                                record_turn_metric(
                                    event="unknown_tools",
                                    increment={"unknown_tool_events": 1},
                                    extra={"unknown_tools": parse_result.unknown_tools},
                                )
                            if tool_calls:
                                record_turn_metric(
                                    event="text_tool_parse_ok",
                                    increment={"tool_batch_parse_success": 1},
                                )
                                if parse_result.recovery_used:
                                    record_turn_metric(
                                        event="text_tool_parse_recovery",
                                        increment={"tool_batch_parse_recovery": 1},
                                        extra={"recovery_methods": parse_result.recovery_methods},
                                    )
                                    log(
                                        f"Text tool batch recovery: "
                                        f"{parse_result.recovery_methods}"
                                    )
                                tool_calls = inject_tool_defaults(
                                    tool_calls,
                                    url=snapshot_url,
                                    tab_id=tab_id if isinstance(tab_id, int) else None,
                                )
                                assistant_msg = {
                                    "content": "",
                                    "tool_calls": tool_calls,
                                }
                                log(f"Text tool batch parsed: {len(tool_calls)} tool(s)")
                                prose_only_streak = 0
                                try:
                                    from .execution_integrity import record_parsed_tools
                                except Exception:
                                    from execution_integrity import record_parsed_tools
                                record_parsed_tools(integrity_state, len(tool_calls))
                            elif parse_result.unknown_tools or parse_result.parse_errors:
                                feedback = build_unknown_tools_nudge(parse_result)
                                if feedback:
                                    tool_messages.append({"role": "user", "content": feedback})
                                    log(f"Unknown/invalid tools: {parse_result.unknown_tools}")
                            elif agentic_active and _action_required and not tool_calls:
                                try:
                                    from .agentic_output_guard import (
                                        build_structured_output_nudge,
                                        contains_manual_code_without_tools,
                                    )
                                except Exception:
                                    from agentic_output_guard import (
                                        build_structured_output_nudge,
                                        contains_manual_code_without_tools,
                                    )
                                if contains_manual_code_without_tools(raw_content):
                                    tool_messages.append({
                                        "role": "user",
                                        "content": build_structured_output_nudge(
                                            prompt=prompt,
                                            reason="Manual code without <agent_tool_batch>",
                                            use_text_tools=True,
                                        ),
                                    })
                                    log("Rejected manual code block — structured tool batch required")

                            if use_text_tools and agentic_active:
                                try:
                                    from .agentic_action_guard import is_actionable_notebook_request
                                    from .agent_tool_refusal import log_tool_refusal_if_applicable
                                except Exception:
                                    from agentic_action_guard import is_actionable_notebook_request
                                    from agent_tool_refusal import log_tool_refusal_if_applicable
                                try:
                                    from .token_usage import extract_usage_from_response
                                except Exception:
                                    from token_usage import extract_usage_from_response
                                _refusal_usage = extract_usage_from_response(tool_resp)
                                log_tool_refusal_if_applicable(
                                    goal=prompt,
                                    round_idx=_round,
                                    raw_model_response=raw_content,
                                    parse_result=parse_result,
                                    parsed_tool_count=len(tool_calls),
                                    action_required=is_actionable_notebook_request(prompt),
                                    prompt_tokens=int(_refusal_usage.get("prompt_tokens") or 0) or None,
                                    response_tokens=int(_refusal_usage.get("completion_tokens") or 0) or None,
                                    messages=tool_messages,
                                    agent_state=agent_state,
                                    session_id=memory_session_id,
                                    notebook_url=snapshot_url,
                                    source="react_round",
                                )
                                try:
                                    from .tool_parser_diagnostics import log_parser_failure_if_applicable
                                except Exception:
                                    from tool_parser_diagnostics import log_parser_failure_if_applicable
                                log_parser_failure_if_applicable(
                                    goal=prompt,
                                    round_idx=_round,
                                    raw_output=raw_content,
                                    parsed_tool_count=len(tool_calls),
                                    action_required=is_actionable_notebook_request(prompt),
                                    parse_result=parse_result,
                                    session_id=memory_session_id,
                                    notebook_url=snapshot_url,
                                    source="react_round",
                                )

                            if plan_generation_pending:
                                agent_state, plan_steps = apply_plan_from_llm_response(
                                    agent_state,
                                    raw_content,
                                    goal=prompt,
                                )
                                if plan_steps:
                                    record_turn_metric(
                                        event="plan_created",
                                        increment={"plan_created": 1},
                                        extra={"step_count": len(plan_steps)},
                                    )
                                    persist_agent_plan(history_key, agent_state)
                                    plan_generation_pending = False
                                    tool_messages.append({
                                        "role": "assistant",
                                        "content": strip_tool_batch_from_text(raw_content),
                                    })
                                    tool_messages.append({
                                        "role": "user",
                                        "content": build_step_execution_nudge(agent_state),
                                    })
                                    log(f"Workflow plan stored: {len(plan_steps)} step(s)")
                                    if tool_calls:
                                        _emit_batch_audit(
                                            session_id=memory_session_id,
                                            round_index=_round,
                                            goal=prompt,
                                            notebook_url=snapshot_url,
                                            parsed_tools=_audit_tool_names(tool_calls),
                                            parsed_tool_count=len(tool_calls),
                                            dispatch_path="plan_generation_skip",
                                            dispatcher_received=False,
                                            executor_called=False,
                                            source_hooks=["streaming.py:~1426 plan_generation_pending continue"],
                                            extra={"plan_steps": len(plan_steps)},
                                        )
                                    _emit_stream_delta(
                                        tab_id=tab_id,
                                        snapshot_url=snapshot_url,
                                        history_key=history_key,
                                        session_id=session_id,
                                        delta=f"\nPlan: {len(plan_steps)} steps — executing step 1…\n",
                                    )
                                    continue
                                if tool_calls:
                                    plan_generation_pending = False
                                    log("Planner: no PLAN block — proceeding with tools directly")

                        elif (
                            not use_text_tools
                            and agentic_active
                            and not tool_calls
                            and is_actionable_notebook_request(prompt)
                        ):
                            raw_native = _final_text_from_response(tool_resp)
                            try:
                                from .agentic_output_guard import (
                                    build_structured_output_nudge,
                                    contains_manual_code_without_tools,
                                )
                            except Exception:
                                from agentic_output_guard import (
                                    build_structured_output_nudge,
                                    contains_manual_code_without_tools,
                                )
                            if contains_manual_code_without_tools(raw_native):
                                tool_messages.append({
                                    "role": "user",
                                    "content": build_structured_output_nudge(
                                        prompt=prompt,
                                        reason="Manual code without native tool_calls",
                                        use_text_tools=False,
                                    ),
                                })
                                log("Rejected manual code block — native tool_calls required")

                        try:
                            from .tool_call_terminal import trace_tools_parsed
                        except Exception:
                            try:
                                from tool_call_terminal import trace_tools_parsed
                            except Exception:
                                trace_tools_parsed = None  # type: ignore
                        if trace_tools_parsed:
                            _recovery = bool(getattr(last_parse_result, "recovery_used", False))
                            _parse_errors = list(getattr(last_parse_result, "parse_errors", None) or [])
                            trace_tools_parsed(
                                _round,
                                tool_calls,
                                source="text" if use_text_tools else "native",
                                recovery=_recovery,
                                parse_errors=_parse_errors if not tool_calls else None,
                            )

                        if (
                            agentic_active
                            and tool_calls
                            and not use_text_tools
                        ):
                            try:
                                from .agentic_action_guard import (
                                    batch_lacks_write_tools,
                                    build_query_only_rejection_message,
                                    is_implementation_request,
                                    is_query_only_tool_batch,
                                )
                            except Exception:
                                from agentic_action_guard import (
                                    batch_lacks_write_tools,
                                    build_query_only_rejection_message,
                                    is_implementation_request,
                                    is_query_only_tool_batch,
                                )
                            _parsed_names = _audit_tool_names(tool_calls)
                            if (
                                is_implementation_request(prompt)
                                and is_query_only_tool_batch(_parsed_names)
                                and batch_lacks_write_tools(_parsed_names)
                                and not AGENTIC_FIRE_AND_FORGET
                            ):
                                reject_msg = build_query_only_rejection_message(
                                    prompt,
                                    parsed_tools=_parsed_names,
                                )
                                final_text = reject_msg
                                _turn_final_assistant_text = reject_msg
                                agentic_turn_resolved = True
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta=("\n" if full_text else "") + reject_msg + "\n",
                                )
                                memory_store.append(
                                    history_key, "assistant", final_text, session_id=memory_session_id
                                )
                                log(
                                    "Rejected query-only tool batch for implementation request: "
                                    + ", ".join(_parsed_names)
                                )
                                _emit_batch_audit(
                                    session_id=memory_session_id,
                                    round_index=_round,
                                    goal=prompt,
                                    notebook_url=snapshot_url,
                                    parsed_tools=_parsed_names,
                                    parsed_tool_count=len(tool_calls),
                                    dispatch_path="query_only_rejected",
                                    dispatcher_received=False,
                                    executor_called=False,
                                    assistant_final_text=reject_msg,
                                    assistant_claimed_success=False,
                                    source_hooks=["streaming.py query_only implementation guard"],
                                )
                                break

                        if (
                            agentic_active
                            and tool_calls
                            and not use_text_tools
                            and incomplete_batch_nudges < MAX_INCOMPLETE_BATCH_NUDGES
                            and _round < max_tool_rounds - 1
                            and not AGENTIC_FIRE_AND_FORGET
                        ):
                            try:
                                from .agentic_action_guard import (
                                    build_incomplete_batch_nudge,
                                    count_implied_tool_actions,
                                )
                            except Exception:
                                from agentic_action_guard import (
                                    build_incomplete_batch_nudge,
                                    count_implied_tool_actions,
                                )
                            implied_actions = count_implied_tool_actions(prompt)
                            parsed_count = len(tool_calls)
                            if implied_actions >= 2 and parsed_count < implied_actions:
                                incomplete_batch_nudges += 1
                                nudge = build_incomplete_batch_nudge(
                                    prompt,
                                    parsed_count=parsed_count,
                                    implied_count=implied_actions,
                                    parsed_tools=_audit_tool_names(tool_calls),
                                    use_text_tools=use_text_tools,
                                )
                                tool_messages.append({"role": "user", "content": nudge})
                                log(
                                    f"Incomplete batch: parsed {parsed_count} tool(s), "
                                    f"implied ~{implied_actions} — nudging LLM"
                                )
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta=(
                                        f"\nIncomplete batch ({parsed_count}/{implied_actions} tools) "
                                        f"— requesting full tool_calls…\n"
                                    ),
                                )
                                continue

                        if not tool_calls:
                            followup = _final_text_from_response(tool_resp).strip()
                            if agentic_active and AGENTIC_FIRE_AND_FORGET:
                                if followup:
                                    tool_messages.append({"role": "assistant", "content": followup})
                                try:
                                    from .agentic_batch_executor import build_fire_and_forget_user_summary
                                except Exception:
                                    from agentic_batch_executor import build_fire_and_forget_user_summary
                                if _ff_cumulative_executed:
                                    summary_verification = {
                                        "executed": list(_ff_cumulative_executed),
                                        "rounds_dispatched": _ff_rounds_dispatched,
                                    }
                                    dispatch_summary = build_fire_and_forget_user_summary(
                                        summary_verification
                                    )
                                    final_text = (
                                        f"{dispatch_summary}\n\n{followup}".strip()
                                        if followup
                                        else dispatch_summary
                                    )
                                elif followup:
                                    final_text = followup
                                else:
                                    final_text = (
                                        "Tools dispatched (fire-and-forget). "
                                        "Check monitor CALL/RESULT trace for outcomes."
                                    )
                                _turn_final_assistant_text = final_text
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta=("\n" if full_text else "") + final_text + "\n",
                                )
                                memory_store.append(
                                    history_key, "assistant", final_text, session_id=memory_session_id
                                )
                                agentic_fire_and_forget_done = True
                                try:
                                    from .tool_call_terminal import trace_react_stop
                                except Exception:
                                    try:
                                        from tool_call_terminal import trace_react_stop
                                    except Exception:
                                        trace_react_stop = None  # type: ignore
                                if trace_react_stop:
                                    trace_react_stop(_round, "prose_done")
                                try:
                                    from .agent_trace import trace_react_event
                                except Exception:
                                    from agent_trace import trace_react_event
                                trace_react_event(
                                    event="react_stop",
                                    round_idx=_round,
                                    stop_reason="prose_done",
                                    extra={"rounds_dispatched": _ff_rounds_dispatched},
                                )
                                break
                            if plan_generation_pending:
                                if followup:
                                    tool_messages.append({"role": "assistant", "content": followup})
                                tool_messages.append({
                                    "role": "user",
                                    "content": build_plan_request_nudge(prompt),
                                })
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta="\nPlan required — please emit numbered PLAN steps…\n",
                                )
                                continue
                            try:
                                from .agentic_action_guard import (
                                    MAX_PROSE_ONLY_ROUNDS,
                                    agentic_must_continue_with_tools,
                                    build_action_nudge,
                                    build_error_recovery_exhausted_message,
                                    build_error_recovery_nudge,
                                    build_prose_only_corrective_nudge,
                                    build_prose_only_exhausted_message,
                                    queue_error_active,
                                )
                            except Exception:
                                from agentic_action_guard import (
                                    MAX_PROSE_ONLY_ROUNDS,
                                    agentic_must_continue_with_tools,
                                    build_action_nudge,
                                    build_error_recovery_exhausted_message,
                                    build_error_recovery_nudge,
                                    build_prose_only_corrective_nudge,
                                    build_prose_only_exhausted_message,
                                    queue_error_active,
                                )
                            try:
                                from .agent_metrics import record_turn_metric
                            except Exception:
                                from agent_metrics import record_turn_metric
                            record_turn_metric(
                                event="prose_only",
                                increment={"prose_only_events": 1, "turns_total": 1},
                            )
                            try:
                                from .tool_call_terminal import trace_prose_only
                            except Exception:
                                try:
                                    from tool_call_terminal import trace_prose_only
                                except Exception:
                                    trace_prose_only = None  # type: ignore
                            if trace_prose_only:
                                trace_prose_only(_round, prose_only_streak + 1)

                            pipeline_active = bool(
                                pipeline_state
                                and pipeline_state.get("active")
                                and not pipeline_state.get("complete")
                            )
                            run_queue_done = last_tool_queue_complete

                            if agentic_active and queue_error_state and _round < max_tool_rounds - 1 and not AGENTIC_FIRE_AND_FORGET:
                                error_recovery_attempts += 1
                                if error_recovery_attempts > MAX_ERROR_RECOVERY_ROUNDS:
                                    final_text = build_error_recovery_exhausted_message(
                                        prompt, queue_error_state
                                    )
                                    log(
                                        f"Error recovery exhausted after {error_recovery_attempts} "
                                        "round(s) without tool batch"
                                    )
                                    _emit_stream_delta(
                                        tab_id=tab_id,
                                        snapshot_url=snapshot_url,
                                        history_key=history_key,
                                        session_id=session_id,
                                        delta=f"\n{final_text}\n",
                                    )
                                    break
                                if followup:
                                    tool_messages.append(
                                        {"role": "assistant", "content": followup}
                                    )
                                nudge = build_error_recovery_nudge(
                                    prompt,
                                    queue_error_state,
                                    use_text_tools=use_text_tools,
                                    no_tools_reply=True,
                                )
                                tool_messages.append({"role": "user", "content": nudge})
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta="\nError recovery — inspect/fix via tools…\n",
                                )
                                continue

                            must_act = agentic_must_continue_with_tools(
                                prompt=prompt,
                                followup_text=followup,
                                tools_executed=agentic_tools_executed,
                                pipeline_active=pipeline_active and not run_queue_done,
                                queue_error_active_flag=bool(queue_error_state),
                            )

                            if agentic_active and must_act and not queue_error_state:
                                prose_only_streak += 1
                                if prose_only_streak >= MAX_PROSE_ONLY_ROUNDS:
                                    final_text = build_prose_only_exhausted_message(
                                        prompt,
                                        streak=prose_only_streak,
                                        use_text_tools=use_text_tools,
                                    )
                                    record_turn_metric(
                                        event="prose_only_early_stop",
                                        increment={
                                            "prose_only_early_stops": 1,
                                            "tasks_failed": 1,
                                        },
                                    )
                                    log(
                                        f"Prose-only limit reached ({prose_only_streak}/"
                                        f"{MAX_PROSE_ONLY_ROUNDS}) — stopping ReAct"
                                    )
                                    _emit_stream_delta(
                                        tab_id=tab_id,
                                        snapshot_url=snapshot_url,
                                        history_key=history_key,
                                        session_id=session_id,
                                        delta=f"\n{final_text}\n",
                                    )
                                    break

                            if agentic_active and must_act and _round < max_tool_rounds - 1 and not AGENTIC_FIRE_AND_FORGET:
                                if followup:
                                    tool_messages.append(
                                        {"role": "assistant", "content": followup}
                                    )
                                if not queue_error_state and prose_only_streak > 0:
                                    nudge = build_prose_only_corrective_nudge(
                                        prompt,
                                        streak=prose_only_streak,
                                        use_text_tools=use_text_tools,
                                    )
                                else:
                                    nudge = build_action_nudge(
                                        prompt,
                                        tools_executed=agentic_tools_executed,
                                        round_idx=_round,
                                        use_text_tools=use_text_tools,
                                    )
                                    if use_text_tools:
                                        nudge += (
                                            "\n\nUse <agent_tool_batch>[...]</agent_tool_batch> with every "
                                            "required tool in one JSON array."
                                        )
                                    else:
                                        nudge += (
                                            "\n\nUse native API tool_calls with every required function "
                                            "in one assistant message (parallel_tool_calls enabled)."
                                        )
                                tool_messages.append({"role": "user", "content": nudge})
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
                                try:
                                    from .agentic_output_guard import should_reject_prose_final
                                except Exception:
                                    from agentic_output_guard import should_reject_prose_final
                                if agentic_active and should_reject_prose_final(
                                    followup,
                                    prompt=prompt,
                                    verification=last_batch_verification,
                                    tools_executed=agentic_tools_executed,
                                ) and _round < max_tool_rounds - 1 and not AGENTIC_FIRE_AND_FORGET:
                                    try:
                                        from .agentic_output_guard import build_structured_output_nudge
                                    except Exception:
                                        from agentic_output_guard import build_structured_output_nudge
                                    tool_messages.append({"role": "assistant", "content": followup})
                                    tool_messages.append({
                                        "role": "user",
                                        "content": build_structured_output_nudge(
                                            prompt=prompt,
                                            reason="Task not verified — tools required",
                                            use_text_tools=use_text_tools,
                                        ),
                                    })
                                    _emit_stream_delta(
                                        tab_id=tab_id,
                                        snapshot_url=snapshot_url,
                                        history_key=history_key,
                                        session_id=session_id,
                                        delta="\nExecution not verified — requesting tool batch…\n",
                                    )
                                    continue
                                final_text = followup
                                if _agentic_status_only(full_text):
                                    full_text = followup + ("\n" if not followup.endswith("\n") else "")
                            _emit_batch_audit(
                                session_id=memory_session_id,
                                round_index=_round,
                                goal=prompt,
                                notebook_url=snapshot_url,
                                parsed_tools=[],
                                parsed_tool_count=0,
                                dispatch_path="prose_only_exit",
                                dispatcher_received=False,
                                executor_called=False,
                                assistant_final_text=followup,
                                assistant_claimed_success=_assistant_claims_success(followup),
                                source_hooks=["streaming.py:~1644 prose-only break"],
                            )
                            break

                        agentic_tools_executed += len(tool_calls)

                        tool_messages.append({
                            "role": "assistant",
                            "content": assistant_msg.get("content") or "",
                            "tool_calls": tool_calls,
                            "_react_tool_batch": True,
                        })

                        reg = _registry_factory()
                        try:
                            from .agentic_batch_executor import (
                                execute_agentic_batch,
                                should_use_batch_executor,
                                workflow_needs_llm_followup,
                                workflow_followup_reason,
                                build_fire_and_forget_user_summary,
                            )
                            from .agentic_mode import browser_tool_allowed as _batch_browser_allowed
                        except Exception:
                            from agentic_batch_executor import (
                                execute_agentic_batch,
                                should_use_batch_executor,
                                workflow_needs_llm_followup,
                                workflow_followup_reason,
                                build_fire_and_forget_user_summary,
                            )
                            from agentic_mode import browser_tool_allowed as _batch_browser_allowed

                        if agentic_active and should_use_batch_executor(tool_calls, agentic_active=True):
                            batch_tool_calls = list(tool_calls)
                            _phase_filter_meta: dict = {}
                            if agentic_active and AGENTIC_FIRE_AND_FORGET and AGENTIC_MANDATORY_TWO_PHASE:
                                try:
                                    from .agentic_batch_executor import filter_tools_for_phase
                                except Exception:
                                    from agentic_batch_executor import filter_tools_for_phase
                                batch_tool_calls, _phase_filter_meta = filter_tools_for_phase(
                                    _round,
                                    batch_tool_calls,
                                    prompt=prompt,
                                    url=snapshot_url,
                                    tab_id=tab_id if isinstance(tab_id, int) else None,
                                    mandatory_two_phase=True,
                                )
                                if _phase_filter_meta.get("writes_stripped"):
                                    log(
                                        "Mandatory two-phase: stripped write tools from round 0 batch"
                                    )
                                if _phase_filter_meta.get("auto_injected"):
                                    log(
                                        "Mandatory two-phase: auto-injected query tool for round 0"
                                    )
                                if _phase_filter_meta.get("queries_stripped"):
                                    log(
                                        "Mandatory two-phase: stripped query tools from round 1 batch"
                                    )
                            try:
                                from .tool_call_terminal import trace_batch_start, trace_dispatch_path
                            except Exception:
                                try:
                                    from tool_call_terminal import trace_batch_start, trace_dispatch_path
                                except Exception:
                                    trace_batch_start = None  # type: ignore
                                    trace_dispatch_path = None  # type: ignore
                            if trace_dispatch_path:
                                trace_dispatch_path("batch_executor", f"{len(batch_tool_calls)} tools")
                            if trace_batch_start:
                                trace_batch_start(_round, batch_tool_calls)
                            try:
                                from .agentic_action_guard import (
                                    batch_lacks_write_tools,
                                    build_query_budget_exhausted_nudge,
                                    build_query_loop_exhausted_message,
                                    cumulative_has_write_tools,
                                    is_query_only_tool_batch,
                                    queue_error_active,
                                    should_force_implementation_batch,
                                )
                            except Exception:
                                from agentic_action_guard import (
                                    batch_lacks_write_tools,
                                    build_query_budget_exhausted_nudge,
                                    build_query_loop_exhausted_message,
                                    cumulative_has_write_tools,
                                    is_query_only_tool_batch,
                                    queue_error_active,
                                    should_force_implementation_batch,
                                )
                            _parsed_batch_names = _audit_tool_names(batch_tool_calls)
                            _ff_has_writes = cumulative_has_write_tools(_ff_cumulative_executed)
                            _force_implementation = (
                                agentic_active
                                and AGENTIC_FIRE_AND_FORGET
                                and should_force_implementation_batch(
                                    prompt=prompt,
                                    parsed_tools=_parsed_batch_names,
                                    query_rounds_used=query_rounds_used,
                                    max_query_rounds=AGENTIC_MAX_QUERY_ROUNDS,
                                    cumulative_has_writes=_ff_has_writes,
                                    round_idx=_round,
                                    max_tool_rounds=max_tool_rounds,
                                    mandatory_two_phase=AGENTIC_MANDATORY_TWO_PHASE,
                                )
                            )
                            if (
                                _force_implementation
                                and consecutive_query_only_rounds >= 2
                                and not _ff_has_writes
                            ):
                                loop_msg = build_query_loop_exhausted_message(prompt)
                                final_text = loop_msg
                                _turn_final_assistant_text = loop_msg
                                agentic_turn_resolved = True
                                _emit_stream_delta(
                                    tab_id=tab_id,
                                    snapshot_url=snapshot_url,
                                    history_key=history_key,
                                    session_id=session_id,
                                    delta=("\n" if full_text else "") + loop_msg + "\n",
                                )
                                memory_store.append(
                                    history_key, "assistant", final_text, session_id=memory_session_id
                                )
                                log("Stopped query-only loop without write dispatch")
                                break
                            verification = execute_agentic_batch(
                                batch_tool_calls,
                                user_prompt=prompt,
                                url=snapshot_url,
                                tab_id=tab_id if isinstance(tab_id, int) else None,
                                registry=reg,
                                browser_tool_allowed=_batch_browser_allowed,
                                mode=resolved_mode,
                                pipeline_state=pipeline_state,
                                cancel_check=_is_stopped,
                                trace_round=_round,
                                force_implementation=_force_implementation,
                            )
                            try:
                                from .tool_call_terminal import trace_batch_end, trace_verification
                            except Exception:
                                try:
                                    from tool_call_terminal import trace_batch_end, trace_verification
                                except Exception:
                                    trace_batch_end = None  # type: ignore
                                    trace_verification = None  # type: ignore
                            if trace_batch_end:
                                trace_batch_end(
                                    _round,
                                    ok=bool(verification.get("verified")),
                                    detail=str(verification.get("goal_reason") or "")[:120],
                                )
                            if trace_verification:
                                trace_verification(_round, verification)
                            if verification.get("cancelled") or _is_stopped():
                                break
                            pipeline_state = verification.get("pipeline") or pipeline_state
                            if verification.get("tool_queue_complete") or verification.get("run_queue_complete"):
                                last_tool_queue_complete = True
                            agentic_had_batch = True
                            last_batch_verification = verification
                            if verification.get("fire_and_forget"):
                                try:
                                    from .agentic_batch_executor import merge_fire_and_forget_executed
                                    from .agentic_verification import append_native_batch_tool_results
                                except Exception:
                                    from agentic_batch_executor import merge_fire_and_forget_executed
                                    from agentic_verification import append_native_batch_tool_results
                                _ff_cumulative_executed = merge_fire_and_forget_executed(
                                    _ff_cumulative_executed,
                                    verification,
                                )
                                _ff_rounds_dispatched += 1
                                last_batch_verification = {
                                    **verification,
                                    "executed": list(_ff_cumulative_executed),
                                    "rounds_dispatched": _ff_rounds_dispatched,
                                }
                                _dispatched_names = _audit_tool_names(batch_tool_calls)
                                if cumulative_has_write_tools(_ff_cumulative_executed):
                                    consecutive_query_only_rounds = 0
                                elif (
                                    not _force_implementation
                                    and is_query_only_tool_batch(_dispatched_names)
                                ):
                                    query_rounds_used += 1
                                    consecutive_query_only_rounds += 1
                                else:
                                    consecutive_query_only_rounds += 1
                                if (
                                    query_rounds_used >= AGENTIC_MAX_QUERY_ROUNDS
                                    and not cumulative_has_write_tools(_ff_cumulative_executed)
                                    and _round < max_tool_rounds - 1
                                ):
                                    tool_messages.append({
                                        "role": "user",
                                        "content": build_query_budget_exhausted_nudge(
                                            prompt,
                                            parsed_tools=_dispatched_names,
                                        ),
                                    })
                                if not use_text_tools:
                                    append_native_batch_tool_results(
                                        tool_messages,
                                        batch_tool_calls,
                                        verification,
                                        round_idx=_round,
                                    )
                                if (
                                    AGENTIC_MANDATORY_TWO_PHASE
                                    and _round == 0
                                    and not phase1_nudge_sent
                                    and _round < max_tool_rounds - 1
                                ):
                                    try:
                                        from .agentic_action_guard import (
                                            build_phase1_implementation_nudge,
                                            build_round0_writes_blocked_nudge,
                                        )
                                    except Exception:
                                        from agentic_action_guard import (
                                            build_phase1_implementation_nudge,
                                            build_round0_writes_blocked_nudge,
                                        )
                                    if _phase_filter_meta.get("writes_stripped"):
                                        nudge_content = build_round0_writes_blocked_nudge(prompt)
                                    else:
                                        nudge_content = build_phase1_implementation_nudge(prompt)
                                    tool_messages.append({
                                        "role": "user",
                                        "content": nudge_content,
                                    })
                                    phase1_nudge_sent = True
                                _ff_has_writes_now = cumulative_has_write_tools(_ff_cumulative_executed)
                                _ff_at_last_round = _round >= max_tool_rounds - 1
                                if _ff_has_writes_now or _ff_at_last_round:
                                    dispatch_summary = build_fire_and_forget_user_summary(
                                        last_batch_verification
                                    )
                                    final_text = dispatch_summary
                                    _turn_final_assistant_text = dispatch_summary
                                    _emit_stream_delta(
                                        tab_id=tab_id,
                                        snapshot_url=snapshot_url,
                                        history_key=history_key,
                                        session_id=session_id,
                                        delta=("\n" if full_text else "") + dispatch_summary + "\n",
                                    )
                                    memory_store.append(
                                        history_key, "assistant", final_text, session_id=memory_session_id
                                    )
                                    agentic_fire_and_forget_done = True
                                    _ff_stop_reason = (
                                        "write_dispatched"
                                        if _ff_has_writes_now
                                        else "max_rounds"
                                    )
                                    try:
                                        from .tool_call_terminal import trace_react_stop
                                    except Exception:
                                        try:
                                            from tool_call_terminal import trace_react_stop
                                        except Exception:
                                            trace_react_stop = None  # type: ignore
                                    if trace_react_stop:
                                        trace_react_stop(_round, _ff_stop_reason)
                                    try:
                                        from .agent_trace import trace_react_event
                                    except Exception:
                                        from agent_trace import trace_react_event
                                    trace_react_event(
                                        event="react_stop",
                                        round_idx=_round,
                                        stop_reason=_ff_stop_reason,
                                        extra={"rounds_dispatched": _ff_rounds_dispatched},
                                    )
                                    break
                                try:
                                    from .agent_trace import trace_react_event
                                except Exception:
                                    from agent_trace import trace_react_event
                                trace_react_event(
                                    event="fire_and_forget_continue",
                                    round_idx=_round,
                                    extra={"rounds_dispatched": _ff_rounds_dispatched},
                                )
                                continue
                            try:
                                from .execution_integrity import update_integrity_from_verification
                            except Exception:
                                from execution_integrity import update_integrity_from_verification
                            update_integrity_from_verification(
                                integrity_state,
                                parsed_tool_count=len(batch_tool_calls),
                                verification=verification,
                                executor_called=True,
                            )
                            if isinstance(last_parse_result, object) and last_parse_result:
                                fb = last_parse_result.to_feedback_dict()
                                if fb.get("unknown_tools") or fb.get("parse_errors"):
                                    verification = dict(verification)
                                    verification["unknown_tools"] = fb.get("unknown_tools") or []
                                    verification["parse_feedback"] = fb
                            try:
                                from .agent_metrics import record_turn_metric
                            except Exception:
                                from agent_metrics import record_turn_metric
                            if verification.get("verified") or verification.get("tool_queue_complete"):
                                record_turn_metric(
                                    event="batch_execution_ok",
                                    increment={"tool_batch_execution_success": 1},
                                )
                            if queue_error_active(verification):
                                record_turn_metric(
                                    event="batch_repair_needed",
                                    increment={"tool_batch_repair_attempts": 1},
                                )
                            try:
                                from .agentic_verification import (
                                    append_batch_verification_message,
                                    append_native_batch_tool_results,
                                    build_compact_batch_verification,
                                )
                            except Exception:
                                from agentic_verification import (
                                    append_batch_verification_message,
                                    append_native_batch_tool_results,
                                    build_compact_batch_verification,
                                )
                            if not use_text_tools:
                                append_native_batch_tool_results(
                                    tool_messages,
                                    batch_tool_calls,
                                    verification,
                                    round_idx=_round,
                                )
                            append_batch_verification_message(
                                tool_messages,
                                verification,
                                round_idx=_round,
                            )
                            agent_state = update_agent_state_from_verification(
                                agent_state,
                                verification,
                                goal=prompt,
                            )
                            if planner_enabled() and agent_state.get("plan"):
                                agent_state, plan_event = update_plan_from_verification(
                                    agent_state,
                                    verification,
                                )
                                if plan_event:
                                    record_turn_metric(
                                        event=plan_event,
                                        increment={plan_event: 1},
                                        extra={
                                            "current_step": agent_state.get("current_step"),
                                            "plan_steps_done": len(
                                                agent_state.get("plan_completed_indices") or []
                                            ),
                                        },
                                    )
                                persist_agent_plan(history_key, agent_state)
                                if (
                                    agent_state.get("current_step") is None
                                    and not queue_error_active(verification)
                                ):
                                    try:
                                        from .agent_planner import clear_agent_plan
                                    except Exception:
                                        from agent_planner import clear_agent_plan
                                    clear_agent_plan(history_key)

                            if verification.get("goal_verified") and verification.get("verified"):
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
                                    status_line = f"\nAll {n} cells verified — summarizing…\n"
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
                            elif verification.get("goal_verified") is False or not verification.get("verified"):
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
                                    reason = verification.get("goal_reason") or "Goal not verified"
                                    err_msg = f"Verification failed: {reason}"
                                    log(err_msg)
                                    _emit_stream_delta(
                                        tab_id=tab_id,
                                        snapshot_url=snapshot_url,
                                        history_key=history_key,
                                        session_id=session_id,
                                        delta=f"\n{err_msg}\nContinuing repair…\n",
                                    )
                                    final_text = (final_text + "\n\n" + err_msg).strip()

                            try:
                                from .agentic_action_guard import (
                                    build_error_recovery_nudge,
                                    queue_error_active,
                                )
                            except Exception:
                                from agentic_action_guard import (
                                    build_error_recovery_nudge,
                                    queue_error_active,
                                )

                            if queue_error_active(verification):
                                queue_error_state = verification
                                error_recovery_attempts = 0
                                recovery_nudge = build_error_recovery_nudge(
                                    prompt,
                                    verification,
                                    use_text_tools=use_text_tools,
                                )
                                tool_messages.append({"role": "user", "content": recovery_nudge})
                                log("Injected queue error recovery nudge for LLM")
                            elif verification.get("tool_queue_complete") or verification.get("run_queue_complete"):
                                queue_error_state = None
                                error_recovery_attempts = 0

                            if workflow_needs_llm_followup(verification):
                                if (
                                    agent_state.get("plan")
                                    and agent_state.get("current_step") is not None
                                ):
                                    tool_messages.append({
                                        "role": "user",
                                        "content": build_step_execution_nudge(agent_state),
                                    })
                                try:
                                    from .agent_trace import trace_react_event
                                except Exception:
                                    from agent_trace import trace_react_event
                                trace_react_event(
                                    event="react_continue",
                                    round_idx=_round,
                                    verification_summary=build_compact_batch_verification(verification),
                                    continue_reason=workflow_followup_reason(verification),
                                    agent_state=agent_state,
                                )
                                try:
                                    from .tool_execution_audit import (
                                        summarize_verification,
                                        tool_records_from_verification,
                                    )
                                except Exception:
                                    from tool_execution_audit import (
                                        summarize_verification,
                                        tool_records_from_verification,
                                    )
                                _emit_batch_audit(
                                    session_id=memory_session_id,
                                    round_index=_round,
                                    goal=prompt,
                                    notebook_url=snapshot_url,
                                    parsed_tools=_audit_tool_names(batch_tool_calls),
                                    parsed_tool_count=len(batch_tool_calls),
                                    parse_recovery_used=bool(
                                        getattr(last_parse_result, "recovery_used", False)
                                    ),
                                    dispatch_path="batch_executor",
                                    dispatcher_received=True,
                                    executor_called=True,
                                    executor_returned=True,
                                    executor_ok=bool(verification.get("batch_executed")),
                                    verification_received=True,
                                    verification_success=bool(verification.get("verified")),
                                    verification_summary=summarize_verification(verification),
                                    workflow_continue=True,
                                    workflow_stop_reason=workflow_followup_reason(verification),
                                    tool_records=tool_records_from_verification(
                                        session_id=memory_session_id,
                                        round_index=_round,
                                        parsed_tools=_audit_tool_names(batch_tool_calls),
                                        verification=verification,
                                    ),
                                    source_hooks=[
                                        "streaming.py:~1679 execute_agentic_batch",
                                        "streaming.py:~1845 workflow_needs_llm_followup continue",
                                    ],
                                )
                                continue
                            try:
                                from .agent_trace import trace_react_event
                            except Exception:
                                from agent_trace import trace_react_event
                            trace_react_event(
                                event="react_stop",
                                round_idx=_round,
                                verification_summary=build_compact_batch_verification(verification),
                                stop_reason=workflow_followup_reason(verification),
                                agent_state=agent_state,
                                extra={"react_llm_calls": react_llm_calls},
                            )
                            try:
                                from .tool_execution_audit import (
                                    summarize_verification,
                                    tool_records_from_verification,
                                )
                            except Exception:
                                from tool_execution_audit import (
                                    summarize_verification,
                                    tool_records_from_verification,
                                )
                            _emit_batch_audit(
                                session_id=memory_session_id,
                                round_index=_round,
                                goal=prompt,
                                notebook_url=snapshot_url,
                                parsed_tools=_audit_tool_names(batch_tool_calls),
                                parsed_tool_count=len(batch_tool_calls),
                                parse_recovery_used=bool(
                                    getattr(last_parse_result, "recovery_used", False)
                                ),
                                dispatch_path="batch_executor",
                                dispatcher_received=True,
                                executor_called=True,
                                executor_returned=True,
                                executor_ok=bool(verification.get("batch_executed")),
                                verification_received=True,
                                verification_success=bool(verification.get("verified")),
                                verification_summary=summarize_verification(verification),
                                workflow_continue=False,
                                workflow_stop_reason=workflow_followup_reason(verification),
                                tool_records=tool_records_from_verification(
                                    session_id=memory_session_id,
                                    round_index=_round,
                                    parsed_tools=_audit_tool_names(batch_tool_calls),
                                    verification=verification,
                                ),
                                source_hooks=[
                                    "streaming.py:~1679 execute_agentic_batch",
                                    "streaming.py:~1870 react_stop break",
                                ],
                            )
                            try:
                                from .tool_call_terminal import trace_react_stop
                            except Exception:
                                try:
                                    from tool_call_terminal import trace_react_stop
                                except Exception:
                                    trace_react_stop = None  # type: ignore
                            if trace_react_stop:
                                trace_react_stop(
                                    _round,
                                    workflow_followup_reason(verification) or "batch complete",
                                )
                            break

                        _seq_executed = []
                        try:
                            from .tool_call_terminal import (
                                log_tool_call,
                                log_tool_result,
                                notebook_slug_from_url,
                                trace_dispatch_path,
                            )
                        except Exception:
                            try:
                                from tool_call_terminal import (
                                    log_tool_call,
                                    log_tool_result,
                                    notebook_slug_from_url,
                                    trace_dispatch_path,
                                )
                            except Exception:
                                log_tool_call = None  # type: ignore
                                log_tool_result = None  # type: ignore
                                notebook_slug_from_url = lambda _u: ""  # type: ignore
                                trace_dispatch_path = None  # type: ignore
                        _seq_slug = notebook_slug_from_url(snapshot_url) or None
                        if trace_dispatch_path:
                            trace_dispatch_path("sequential", f"{len(tool_calls)} tools")
                        for tc_idx, tc in enumerate(tool_calls):
                            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                            fname = fn.get("name")
                            raw_args = fn.get("arguments") or "{}"
                            try:
                                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                            except Exception:
                                parsed_args = {}
                            try:
                                from .bot_tool_utils import coerce_notebook_tool_session
                            except Exception:
                                from bot_tool_utils import coerce_notebook_tool_session
                            parsed_args = coerce_notebook_tool_session(
                                parsed_args,
                                session_url=snapshot_url,
                                session_tab_id=tab_id if isinstance(tab_id, int) else None,
                                tool_name=str(fname or ""),
                            )

                            if log_tool_call:
                                log_tool_call(
                                    str(fname or "?"),
                                    parsed_args,
                                    phase="sequential",
                                    round_idx=_round,
                                    notebook_slug=_seq_slug,
                                )
                            try:
                                allowed, block_err = browser_tool_allowed(resolved_mode, str(fname or ""))
                                if not allowed:
                                    result = {"ok": False, "error": block_err, "tool": fname}
                                else:
                                    result = reg.call(fname, parsed_args)
                            except Exception as exc:
                                result = {"ok": False, "error": str(exc)}

                            if log_tool_result:
                                log_tool_result(
                                    str(fname or "?"),
                                    parsed_args,
                                    result if isinstance(result, dict) else {"ok": False},
                                    phase="sequential",
                                    round_idx=_round,
                                    notebook_slug=_seq_slug,
                                )

                            _seq_executed.append(
                                {
                                    "tool": fname,
                                    "dispatched": bool((result or {}).get("ok")),
                                    "phase": "sequential",
                                    "result_ok": (result or {}).get("ok"),
                                    "error": (result or {}).get("error"),
                                }
                            )

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

                        if _seq_executed:
                            try:
                                from .tool_execution_audit import tool_records_from_verification
                            except Exception:
                                from tool_execution_audit import tool_records_from_verification
                            _fake_verification = {
                                "verified": all(x.get("result_ok") for x in _seq_executed),
                                "executed": _seq_executed,
                                "batch_executed": True,
                            }
                            try:
                                from .execution_integrity import update_integrity_from_verification
                            except Exception:
                                from execution_integrity import update_integrity_from_verification
                            update_integrity_from_verification(
                                integrity_state,
                                parsed_tool_count=len(tool_calls),
                                verification=_fake_verification,
                                executor_called=True,
                            )
                            _emit_batch_audit(
                                session_id=memory_session_id,
                                round_index=_round,
                                goal=prompt,
                                notebook_url=snapshot_url,
                                parsed_tools=_audit_tool_names(tool_calls),
                                parsed_tool_count=len(tool_calls),
                                dispatch_path="sequential_registry",
                                dispatcher_received=True,
                                executor_called=True,
                                executor_returned=True,
                                executor_ok=all(x.get("result_ok") for x in _seq_executed),
                                verification_received=False,
                                verification_success=None,
                                tool_records=tool_records_from_verification(
                                    session_id=memory_session_id,
                                    round_index=_round,
                                    parsed_tools=_audit_tool_names(tool_calls),
                                    verification=_fake_verification,
                                ),
                                source_hooks=["streaming.py:~1880 sequential reg.call loop"],
                            )

                    if agentic_active and AGENTIC_FIRE_AND_FORGET and (
                        agentic_fire_and_forget_done or agentic_had_batch or agentic_tools_executed > 0
                    ):
                        if not final_text.strip() or _agentic_status_only(final_text):
                            try:
                                from .agentic_batch_executor import build_fire_and_forget_user_summary
                            except Exception:
                                from agentic_batch_executor import build_fire_and_forget_user_summary
                            if last_batch_verification and last_batch_verification.get("executed"):
                                summary_payload = dict(last_batch_verification)
                                if _ff_cumulative_executed:
                                    summary_payload["executed"] = list(_ff_cumulative_executed)
                                    summary_payload["rounds_dispatched"] = _ff_rounds_dispatched
                                dispatch_summary = build_fire_and_forget_user_summary(
                                    summary_payload
                                )
                            elif _seq_executed:
                                dispatch_summary = build_fire_and_forget_user_summary(
                                    {"executed": list(_seq_executed)}
                                )
                            else:
                                dispatch_summary = (
                                    "Tools dispatched (fire-and-forget). "
                                    "Check monitor CALL/RESULT trace for outcomes."
                                )
                            final_text = dispatch_summary
                            _turn_final_assistant_text = dispatch_summary
                            _emit_stream_delta(
                                tab_id=tab_id,
                                snapshot_url=snapshot_url,
                                history_key=history_key,
                                session_id=session_id,
                                delta=("\n" if full_text else "") + dispatch_summary + "\n",
                            )
                            memory_store.append(
                                history_key, "assistant", final_text, session_id=memory_session_id
                            )
                    if not _is_stopped() and not agentic_turn_resolved and not (
                        agentic_active
                        and AGENTIC_FIRE_AND_FORGET
                        and (agentic_fire_and_forget_done or agentic_had_batch or agentic_tools_executed > 0)
                    ):
                        final_attempt = _begin_llm_request(_is_stopped)
                        if final_attempt:
                            try:
                                try:
                                    from .context_budget import fit_react_messages_to_budget, messages_for_api
                                except Exception:
                                    from context_budget import fit_react_messages_to_budget, messages_for_api
                                tool_messages = inject_agent_state_message(tool_messages, agent_state)
                                tool_messages, _ = fit_react_messages_to_budget(
                                    tool_messages,
                                    original_user_prompt=prompt,
                                )
                                final_resp = _LLM_CLIENT.chat.completions.create(
                                    **_augment_llm_create_kwargs(
                                        {
                                            "messages": messages_for_api(tool_messages),
                                            "model": LLM_MODEL,
                                            "temperature": TEMPERATURE,
                                            "top_p": TOP_P,
                                            **completion_extra,
                                        },
                                        messages=tool_messages,
                                    )
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
                                    _turn_final_assistant_text = followup
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

        if llm_request_failed and llm_failure_message.strip():
            final_text = llm_failure_message
        elif agentic_active and not final_text.strip():
            final_text = (
                "Agentic request finished without a response. "
                "Restart the host after code updates and check host.log for API/tool errors."
            )
        elif agentic_active and _agentic_status_only(final_text):
            final_text = (
                "Tools ran but no summary was returned. "
                "Check testing/host/data/logs/host.log for errors, or restart the host after code updates."
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
                try:
                    from .agent_tool_refusal import log_tool_refusal_event
                except Exception:
                    from agent_tool_refusal import log_tool_refusal_event
                log_tool_refusal_event(
                    goal=prompt,
                    round_idx=-1,
                    raw_model_response=final_text,
                    tool_batch_found=False,
                    parsed_tool_count=0,
                    action_required=True,
                    session_id=memory_session_id,
                    notebook_url=snapshot_url,
                    extra={
                        "source": "final_instruction_only_guard",
                        "failure_type": "PROSE_ONLY",
                        "host_message": (
                            "Agentic mode requires tool execution, but the model returned "
                            "manual instructions instead of tool calls."
                        ),
                    },
                )
                final_text = (
                    "Agentic mode requires tool execution, but the model returned "
                    "manual instructions instead of tool calls. Retry the request."
                )
                _emit_batch_audit(
                    session_id=memory_session_id,
                    round_index=-1,
                    goal=prompt,
                    notebook_url=snapshot_url,
                    parsed_tools=[],
                    parsed_tool_count=0,
                    dispatch_path="final_instruction_only_guard",
                    dispatcher_received=False,
                    executor_called=False,
                    assistant_final_text=final_text,
                    assistant_claimed_success=False,
                    source_hooks=["streaming.py:~2050 final_instruction_only_guard"],
                    extra={"host_message": final_text},
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

        was_stopped = _is_stopped()
        if agentic_active and final_text.strip():
            try:
                from .agentic_action_guard import is_actionable_notebook_request
            except Exception:
                from agentic_action_guard import is_actionable_notebook_request
            _action_required_final = is_actionable_notebook_request(prompt)
            if last_batch_verification:
                try:
                    from .agent_goal_verification import sanitize_false_success_language
                except Exception:
                    from agent_goal_verification import sanitize_false_success_language
                final_text = sanitize_false_success_language(
                    final_text, last_batch_verification
                )
            try:
                from .execution_integrity import apply_final_integrity_gate
            except Exception:
                from execution_integrity import apply_final_integrity_gate
            final_text, _integrity_blocked = apply_final_integrity_gate(
                final_text,
                integrity_state,
                verification=last_batch_verification,
                action_required=_action_required_final,
                goal=prompt,
                session_id=memory_session_id,
                round_index=-2,
                llm_request_failed=llm_request_failed,
            )
            if _integrity_blocked:
                log(
                    f"Execution integrity gate blocked false success "
                    f"(reason={integrity_state.block_reason})"
                )
            _turn_final_assistant_text = final_text
            try:
                from .tool_execution_audit import (
                    build_batch_record,
                    log_batch_audit,
                    summarize_verification,
                )
            except Exception:
                from tool_execution_audit import (
                    build_batch_record,
                    log_batch_audit,
                    summarize_verification,
                )
            log_batch_audit(
                build_batch_record(
                    session_id=memory_session_id,
                    round_index=-2,
                    goal=prompt,
                    notebook_url=snapshot_url,
                    parsed_tool_count=integrity_state.parsed_tool_count,
                    dispatch_path="turn_final_summary",
                    verification_received=integrity_state.verification_received,
                    verification_success=integrity_state.verification_success,
                    verification_summary=(
                        summarize_verification(last_batch_verification)
                        if last_batch_verification
                        else None
                    ),
                    assistant_final_text=final_text,
                    assistant_claimed_success=_assistant_claims_success(final_text),
                    source_hooks=["execution_integrity.apply_final_integrity_gate"],
                    extra={
                        "agentic_tools_executed": agentic_tools_executed,
                        "agentic_had_batch": agentic_had_batch,
                        "goal_verified": integrity_state.goal_verified,
                        "integrity_blocked": _integrity_blocked,
                        "block_reason": integrity_state.block_reason,
                    },
                )
            )
        if was_stopped and _agentic_status_only(final_text):
            final_text = ""
        if final_text and not was_stopped:
            memory_store.append(history_key, "assistant", final_text, session_id=memory_session_id)

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
        send_msg({"type": "CHAT_STREAM_END", "error": str(e), "stopped": _is_stopped(), "tabId": tab_id, "url": snapshot_url, "notebookKey": history_key, "sessionId": session_id})
    finally:
        clear_active_stream(active_key, session_id)
