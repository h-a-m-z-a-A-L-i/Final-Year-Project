import json
import re
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
        _CEREBRAS_CLIENT,
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
            _CEREBRAS_CLIENT,
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


def inject_prefetched_tool_context(
    messages: list,
    *,
    graph=None,
    cell_slice: str | None = None,
    placement: dict | None = None,
) -> None:
    """
    Attach eager tool results to the system prompt.

    Cerebras requires tool-role messages to include tool_call_id and follow an
    assistant message with tool_calls. Prefetch is not a real tool round-trip, so
    we merge results into system content instead.
    """
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
        return

    block = (
        "## Prefetched tool results\n"
        "Use this evidence for your answer. For placement, follow `notebook_recommend_placement` "
        "(insert NEW code cell below the defining cell — not a distant empty cell).\n\n"
        + "\n\n".join(parts)
    )

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


def _finalize_request_attempt(attempt_id: str, tokens: int):
    with _RATE_LOCK:
        tracker = _prune_rate_tracker(_load_rate_tracker())
        for event in reversed(tracker.get("events", [])):
            if event.get("id") == attempt_id:
                event["tokens"] = int(tokens)
                break
        _save_rate_tracker(tracker)


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


def _chunk_text_from_event(event) -> str:
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
        choices = getattr(event, "choices", None) or []
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            return ""
        return _extract_text(getattr(delta, "content", None))
    except Exception:
        return ""


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
            text = _extract_text(getattr(message, "content", None))
            if text:
                return text
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


def send_stream_end(url, tab_id, session_id, *, response="", stopped=False, error=None):
    payload = {
        "type": "CHAT_STREAM_END",
        "response": response,
        "stopped": stopped,
        "tabId": tab_id,
        "url": url,
        "sessionId": session_id,
    }
    if error:
        payload["error"] = error
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


def _run_streaming_chat(url, prompt, tab_id, session_id, history, context, mode, explicit_mode=None, context_meta=None):
    full_text = ""
    active_key = str(tab_id)
    attempt_id = ""
    state = None
    response = None

    def _is_stopped() -> bool:
        with _ACTIVE_STREAMS_LOCK:
            current = _ACTIVE_STREAMS.get(active_key)
            return bool(current and current.get("stopped"))

    if _CEREBRAS_CLIENT is None:
        err = "Missing CEREBRAS_API_KEY environment variable."
        log(err)
        send_msg({"type": "CHAT_RESPONSE", "error": err, "tabId": tab_id, "url": url, "sessionId": session_id})
        send_msg({"type": "CHAT_STREAM_END", "error": err, "stopped": False, "tabId": tab_id, "url": url, "sessionId": session_id})
        return

    try:
        try:
            from .prompt_engineering import detect_mode, build_chat_messages, normalize_mode
        except Exception:
            from prompt_engineering import detect_mode, build_chat_messages, normalize_mode

        context_meta = context_meta if isinstance(context_meta, dict) else {}
        resolved_mode = detect_mode(
            prompt,
            explicit_mode or mode,
            has_cell_context=context_meta.get("cell_index") is not None,
        )
        resolved_mode = normalize_mode(resolved_mode)
        log(f"AI Stream Request for {url} (session={session_id}, model={CEREBRAS_MODEL}, mode={resolved_mode})")

        messages = build_chat_messages(
            mode=resolved_mode,
            user_prompt=prompt,
            history=history,
            context=context,
            notebook_url=url,
            include_tools=True,
        )

        pre_stream_tools_done = False
        try:
            from .notebook_context import TOOL_FIRST_MODES, cell_slice_from_snapshot
        except Exception:
            from notebook_context import TOOL_FIRST_MODES, cell_slice_from_snapshot

        if _is_stopped():
            send_stream_end(url, tab_id, session_id, stopped=True)
            return

        coverage = str(context_meta.get("coverage") or "none")
        if resolved_mode in TOOL_FIRST_MODES and coverage in ("none", "partial"):
            if _is_stopped():
                send_stream_end(url, tab_id, session_id, stopped=True)
                return
            try:
                from .tool_registry import registry as _registry_factory
            except Exception:
                from tool_registry import registry as _registry_factory
            reg = _registry_factory()
            try:
                from .local_notebook_tools import extract_symbols_from_text
            except Exception:
                from local_notebook_tools import extract_symbols_from_text

            status = reg.call("notebook_snapshot_status", {"url": url})
            graph = reg.call("notebook_graph_query", {"url": url})
            cell_payload = None
            cell_idx = context_meta.get("cell_index")
            if cell_idx is not None:
                try:
                    cell_idx = int(cell_idx)
                    cell_payload = reg.call(
                        "notebook_get_cell",
                        {"url": url, "cell_index": cell_idx, "include_output": True},
                    )
                except Exception as e:
                    log(f"Pre-stream get_cell failed: {e}")

            placement_payload = None
            symbols = extract_symbols_from_text(prompt)
            wants_placement = bool(symbols)
            if wants_placement:
                try:
                    placement_payload = reg.call(
                        "notebook_recommend_placement",
                        {"url": url, "symbols": symbols},
                    )
                except Exception as e:
                    log(f"Pre-stream placement recommend failed: {e}")

            inject_prefetched_tool_context(
                messages,
                graph={"status": status, "graph": graph},
                cell_slice=json.dumps(cell_payload, ensure_ascii=False) if cell_payload else None,
                placement=placement_payload,
            )
            pre_stream_tools_done = True

        try:
            from .context_budget import fit_messages_to_budget, estimate_messages_tokens
        except Exception:
            from context_budget import fit_messages_to_budget, estimate_messages_tokens
        messages = fit_messages_to_budget(messages)
        log(f"Prompt budget: ~{estimate_messages_tokens(messages)} est. tokens, {len(messages)} messages")

        while True:
            if _is_stopped():
                break

            if not _wait_for_request_slot(cancel_check=_is_stopped):
                break

            if _is_stopped():
                break

            attempt_id = str(uuid.uuid4())
            _record_request_attempt(attempt_id)
            allowed, details = _check_token_limits()
            if not allowed:
                raise Exception(f"Local rate limit hit: {details}")
            try:
                stream = _CEREBRAS_CLIENT.chat.completions.create(
                    messages=messages,
                    model=CEREBRAS_MODEL,
                    stream=True,
                    temperature=TEMPERATURE,
                    top_p=TOP_P,
                )
                break
            except Exception as stream_error:
                err = str(stream_error)
                if _is_stopped():
                    break
                if "429" in err or "queue_exceeded" in err or "too_many_requests_error" in err:
                    log("Queue busy. Retrying in 2.5s...")
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
                "url": url,
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
                "url": url,
                "sessionId": session_id,
            })
            return

        for event in stream:
            with _ACTIVE_STREAMS_LOCK:
                state = _ACTIVE_STREAMS.get(active_key)
            if not state or state.get("stopped"):
                break

            chunk = _chunk_text_from_event(event)
            if not chunk:
                continue

            full_text += chunk
            send_msg({
                "type": "CHAT_STREAM",
                "delta": chunk,
                "tabId": tab_id,
                "url": url,
                "sessionId": session_id,
            })

        with _ACTIVE_STREAMS_LOCK:
            state = _ACTIVE_STREAMS.get(active_key) or state or {}
            was_stopped = bool(state.get("stopped"))

        if was_stopped:
            if attempt_id:
                _finalize_request_attempt(attempt_id, 0)
            send_stream_end(
                url,
                tab_id,
                session_id,
                response=full_text.strip(),
                stopped=True,
            )
            return

        if not was_stopped and not full_text.strip():
            messages = fit_messages_to_budget(messages)
            response = _CEREBRAS_CLIENT.chat.completions.create(
                messages=messages,
                model=CEREBRAS_MODEL,
                temperature=TEMPERATURE,
                top_p=TOP_P,
            )
            full_text = _final_text_from_response(response)

        final_text = full_text.strip()
        # Estimate tokens for local limiter accounting in stream mode.
        estimated_tokens = len(str(prompt or "").split()) + len(final_text.split())
        if attempt_id:
            _finalize_request_attempt(attempt_id, estimated_tokens)

        if final_text and not was_stopped:
            memory_store.append(url, "assistant", final_text, session_id=session_id)

        skip_post_stream_tools = (
            resolved_mode in TOOL_FIRST_MODES
            and (pre_stream_tools_done or coverage == "full")
        )

        # Tool calling via Cerebras tools API (local JSON read tools by default).
        try:
            from .tool_registry import registry as _registry_factory, build_cerebras_tools

            tools = build_cerebras_tools()
            if tools and not was_stopped and not skip_post_stream_tools:
                tool_messages = list(messages)
                if final_text:
                    tool_messages.append({"role": "assistant", "content": final_text})

                for _round in range(3):
                    if _is_stopped():
                        break
                    try:
                        tool_messages = fit_messages_to_budget(tool_messages)
                        tool_resp = _CEREBRAS_CLIENT.chat.completions.create(
                            messages=tool_messages,
                            model=CEREBRAS_MODEL,
                            tools=tools,
                            parallel_tool_calls=False,
                            temperature=TEMPERATURE,
                            top_p=TOP_P,
                        )
                    except Exception as e:
                        log(f"Tool-enabled model call failed: {e}")
                        break

                    dumped = tool_resp.model_dump() if hasattr(tool_resp, "model_dump") else {}
                    choice = (dumped.get("choices") or [{}])[0]
                    assistant_msg = choice.get("message") or {}
                    tool_calls = assistant_msg.get("tool_calls") or []

                    if not tool_calls:
                        followup = _final_text_from_response(tool_resp).strip()
                        if followup:
                            final_text = followup
                        break

                    tool_messages.append({
                        "role": "assistant",
                        "content": assistant_msg.get("content") or "",
                        "tool_calls": tool_calls,
                    })

                    reg = _registry_factory()
                    for tc in tool_calls:
                        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                        fname = fn.get("name")
                        raw_args = fn.get("arguments") or "{}"
                        try:
                            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                        except Exception:
                            parsed_args = {}
                        parsed_args.setdefault("url", url)
                        if isinstance(tab_id, int) and tab_id > 0:
                            parsed_args.setdefault("tab_id", tab_id)

                        try:
                            result = reg.call(fname, parsed_args)
                        except Exception as exc:
                            result = {"ok": False, "error": str(exc)}

                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": json.dumps(result, ensure_ascii=False),
                        })

                        if isinstance(result, dict) and result.get("ok") is False:
                            err_msg = f"Tool '{fname}' failed: {result.get('error')}"
                            log(err_msg)
                            final_text = (final_text + "\n\n" + err_msg).strip()

                if not _is_stopped():
                    try:
                        tool_messages = fit_messages_to_budget(tool_messages)
                        final_resp = _CEREBRAS_CLIENT.chat.completions.create(
                            messages=tool_messages,
                            model=CEREBRAS_MODEL,
                            temperature=TEMPERATURE,
                            top_p=TOP_P,
                        )
                        followup = _final_text_from_response(final_resp).strip()
                        if followup:
                            final_text = followup
                            memory_store.append(url, "assistant", final_text, session_id=session_id)
                    except Exception as e:
                        log(f"Final model call after tools failed: {e}")
        except Exception as e:
            log(f"Orchestration error: {e}")

        send_stream_end(
            url,
            tab_id,
            session_id,
            response=final_text,
            stopped=was_stopped,
        )

    except Exception as e:
        err_text = f"Error: {e}"
        log(f"AI Stream Error: {e}")
        if attempt_id:
            _finalize_request_attempt(attempt_id, 0)
        if not _is_stopped():
            memory_store.append(url, "assistant", err_text, session_id=session_id)
        send_msg({"type": "CHAT_RESPONSE", "error": str(e), "tabId": tab_id, "url": url, "sessionId": session_id})
        send_msg({"type": "CHAT_STREAM_END", "error": str(e), "stopped": False, "tabId": tab_id, "url": url, "sessionId": session_id})
    finally:
        clear_active_stream(active_key, session_id)
