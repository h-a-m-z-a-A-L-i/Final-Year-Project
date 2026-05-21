import time
import uuid
from datetime import datetime, timezone, timedelta
from .config import *
from .memory import memory_store
from .dispatcher import send_msg


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


def _signal_remote_stop(session_id: str):
    with _ACTIVE_STREAMS_LOCK:
        for k, state in list(_ACTIVE_STREAMS.items()):
            if state.get("sessionId") == session_id:
                _stop_active_stream(state)


def _run_streaming_chat(url, prompt, tab_id, session_id, history, context, mode):
    full_text = ""
    active_key = str(tab_id)
    attempt_id = ""
    state = None

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
        log(f"AI Stream Request for {url} (session={session_id}, model={CEREBRAS_MODEL})")

        messages = []
        if context:
            messages.append({
                "role": "system",
                "content": f"Mode: {mode}\n\nContext:\n{context}",
            })
        for h in history or []:
            role = str(h.get("role", "")).strip().lower()
            content = str(h.get("content", ""))
            if role in {"user", "assistant", "system"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": str(prompt or "")})

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
                    if _is_stopped():
                        break
                    time.sleep(2.5)
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

        if not was_stopped and not full_text.strip():
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

        send_msg({
            "type": "CHAT_STREAM_END",
            "response": final_text,
            "stopped": was_stopped,
            "tabId": tab_id,
            "url": url,
            "sessionId": session_id,
        })

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
        with _ACTIVE_STREAMS_LOCK:
            current = _ACTIVE_STREAMS.get(active_key)
            if current and current.get("sessionId") == session_id:
                _ACTIVE_STREAMS.pop(active_key, None)
