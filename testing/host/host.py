import json, struct, sys, threading, re, sqlite3, os, ast, uuid
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from cerebras.cloud.sdk import Cerebras
try:
    import persistence
except Exception:
    persistence = None
try:
    from . import jsonl_queue
except Exception:
    jsonl_queue = None

# Import centralized config and dispatcher helpers (support running as script)
try:
    from .config import *
    from .config import _HASHES_LOCK, _EXECUTION_STATE_LOCK, _SEND_LOCK, _ACTIVE_STREAMS, _ACTIVE_STREAMS_LOCK, _RATE_LOCK, _BOT_STATE_LOCK, _CEREBRAS_CLIENT
    from .dispatcher import read_msg, send_msg, _append_jsonl, _start_bot_command_watcher
except Exception:
    # When executed as a script (no package context), import from the filesystem
    repo_root = Path(__file__).resolve().parents[2]
    host_pkg = Path(__file__).resolve().parent
    if str(host_pkg) not in sys.path:
        sys.path.insert(0, str(host_pkg))
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from config import *
        from config import _HASHES_LOCK, _EXECUTION_STATE_LOCK, _SEND_LOCK, _ACTIVE_STREAMS, _ACTIVE_STREAMS_LOCK, _RATE_LOCK, _BOT_STATE_LOCK, _CEREBRAS_CLIENT
        from dispatcher import read_msg, send_msg, _append_jsonl, _start_bot_command_watcher
    except Exception:
        # As a final fallback try package-style import using repo folder name
        from testing.host.config import *
        from testing.host.config import _HASHES_LOCK, _EXECUTION_STATE_LOCK, _SEND_LOCK, _ACTIVE_STREAMS, _ACTIVE_STREAMS_LOCK, _RATE_LOCK, _BOT_STATE_LOCK, _CEREBRAS_CLIENT
        from testing.host.dispatcher import read_msg, send_msg, _append_jsonl, _start_bot_command_watcher

# configuration comes from testing/host/config.py

def _normalized_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.split('#', 1)[0].split('?', 1)[0].rstrip('/')
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), (parsed.path or '').rstrip('/'), "", "", ""))
    except Exception:
        return raw.split('#', 1)[0].split('?', 1)[0].rstrip('/')

def _history_url_key(url: str) -> str:
    return _normalized_url(url)

def get_safe_filename(url: str) -> str:
    safe_name = "".join(c if c.isalnum() else "_" for c in _normalized_url(url)).strip("_")
    return f"{safe_name[:200]}.json"

def _live_dir() -> Path:
    d = SCRAPED_DIR / "live"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _persistent_dir() -> Path:
    d = SCRAPED_DIR / "persistent"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _atomic_write_json(file_path: Path, data):
    if persistence:
        return persistence.atomic_write_json(file_path, data)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(file_path)

def save_live_json(data, tab_url):
    filename = get_safe_filename(tab_url)
    path = _live_dir() / filename
    _atomic_write_json(path, data)
    return path

def save_persistent_json(data, tab_url):
    filename = get_safe_filename(tab_url)
    path = _persistent_dir() / filename
    _atomic_write_json(path, data)
    legacy_path = SCRAPED_DIR / filename
    _atomic_write_json(legacy_path, data)
    return path

def _signal_remote_stop(session_id: str):
    # No remote stop endpoint is needed with direct SDK streaming.
    return

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

def _load_rate_tracker() -> dict:
    if not RATE_LIMIT_TRACKER.exists():
        return {"events": []}
    try:
        data = json.loads(RATE_LIMIT_TRACKER.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"events": []}
        # Backward compatibility for older tracker shape.
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

try:
    from .persistence_helpers import (
        _atomic_write_json,
        save_json,
        save_live_json,
        save_persistent_json,
        get_safe_filename,
        _load_hashes,
        _save_hashes,
        _load_execution_state,
        _save_execution_state,
    )
except Exception:
    from persistence_helpers import (
        _atomic_write_json,
        save_json,
        save_live_json,
        save_persistent_json,
        get_safe_filename,
        _load_hashes,
        _save_hashes,
        _load_execution_state,
        _save_execution_state,
    )

def _system_time_label() -> str:
    return datetime.now().strftime("%I:%M%p").lstrip("0").lower()

def _cell_execution_title(execution_order, execution_timestamp=None):
    """Generate execution title from order and optional timestamp (ISO format)."""
    if execution_order is None:
        return ""
    
    # If timestamp provided (from flag system), use it; otherwise use current time
    if execution_timestamp:
        try:
            dt = datetime.fromisoformat(execution_timestamp)
            time_label = dt.strftime("%I:%M%p").lstrip("0").lower()
            return "Cell executed at " + time_label
        except:
            pass
    
    # Fallback: no valid timestamp available — return empty, never stamp wrong time.
    return ""

def _normalize_kernel_scenario(kernel_scenario: str) -> str:
    return str(kernel_scenario or "unknown").strip().lower()

def _kernel_is_active(kernel_status: str) -> bool:
    return str(kernel_status or "").strip().lower() == "running"

def _scenario_is_fresh(kernel_scenario: str) -> bool:
    return _normalize_kernel_scenario(kernel_scenario) == "scenario_2_fresh_kernel_started"

def _scenario_is_reload(kernel_scenario: str) -> bool:
    return _normalize_kernel_scenario(kernel_scenario) == "scenario_3_reload_running_kernel"

def _scenario_is_off(kernel_scenario: str) -> bool:
    return _normalize_kernel_scenario(kernel_scenario) == "scenario_1_new_notebook_off"

def _default_execution_snapshot(cell_index: int, source: str, output: str) -> dict:
    return {
        "index": cell_index,
        "input": source,
        "output": output,
        "execution_order": None,
        "execution_title": "",
        "execution_timestamp": None,
    }

try:
    from .dependency import _build_fallback_graph, DependencyManager
except Exception:
    try:
        from dependency import _build_fallback_graph, DependencyManager
    except Exception:
        from testing.host.dependency import _build_fallback_graph, DependencyManager

# Dependency engine availability flags
try:
    from dependency_tracker import DependencyTracker
    from context_builder import ContextBuilder
    _DEP_AVAILABLE = True
    _DEP_FALLBACK = False
except Exception:
    _DEP_AVAILABLE = False
    _DEP_FALLBACK = False

class DependencyManager:
    """Manages ContextBuilder instances for each notebook, loading from SCRAPED_DIR."""
    def __init__(self, json_dir: Path):
        self.json_dir = json_dir
        self._cache = {} # filename -> {builder, mtime}

    def get_builder(self, notebook_url: str):
        if not _DEP_AVAILABLE or not notebook_url: return None
        filename = get_safe_filename(notebook_url)
        candidates = [_persistent_dir() / filename]
        json_path = None
        for p in candidates:
            if p.exists():
                json_path = p
                break
        if not json_path:
            return None

        mtime = json_path.stat().st_mtime
        if filename not in self._cache or self._cache[filename]['mtime'] != mtime:
            log(f"Building graph for {filename}")
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cells = data.get('cells', [])
                tracker = DependencyTracker()
                cells_data = {}
                for cell in cells:
                    idx = cell.get('index', 0)
                    code = cell.get('input', '')
                    output = cell.get('output', '')
                    cells_data[idx] = {'code': code, 'output': output}
                    tracker.update_cell(idx, code)
                tracker.update_all_reverse_dependencies()
                self._cache[filename] = {
                    'builder': ContextBuilder(tracker, cells_data),
                    'mtime': mtime,
                    'cell_count': len(cells_data)
                }
            except Exception as e:
                log(f"Failed to build graph: {e}")
                return None
        return self._cache[filename]['builder']

class LocalMemoryStore:
    """Handles persistent SQLite chat history per notebook."""
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=DB_TIMEOUT_SECONDS)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._connect() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, notebook_url TEXT, session_id TEXT NOT NULL DEFAULT 'default', role TEXT, content TEXT, timestamp TEXT)")
                conn.execute("CREATE TABLE IF NOT EXISTS profile_facts (notebook_url TEXT NOT NULL, fact_key TEXT NOT NULL, fact_value TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(notebook_url, fact_key))")
                # Backward-compatible migration for existing databases.
                existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
                if "session_id" not in existing_cols:
                    conn.execute("ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
                conn.execute("UPDATE messages SET session_id = 'default' WHERE session_id IS NULL OR TRIM(session_id) = ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_notebook_url_id ON messages(notebook_url, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_url_session_id ON messages(notebook_url, session_id, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_profile_facts_notebook_url ON profile_facts(notebook_url)")
                conn.commit()

    def append(self, url, role, content, session_id="default"):
        sid = str(session_id or "default")
        with self._lock:
            with self._connect() as conn:
                conn.execute("INSERT INTO messages (notebook_url, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                             (url, sid, role, content, datetime.now(timezone.utc).isoformat()))
                conn.commit()

    def get_history(self, url, limit=MAX_HISTORY_MESSAGES, session_id="default"):
        sid = str(session_id or "default")
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute("SELECT role, content FROM messages WHERE notebook_url = ? AND session_id = ? ORDER BY id DESC LIMIT ?", (url, sid, limit))
                rows = cursor.fetchall()
                return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def list_sessions(self, url, limit=30):
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT session_id, COUNT(*) as message_count, MAX(id) as last_id
                    FROM messages
                    WHERE notebook_url = ?
                    GROUP BY session_id
                    ORDER BY last_id DESC
                    LIMIT ?
                    """,
                    (url, limit),
                ).fetchall()
                return [
                    {
                        "sessionId": r[0],
                        "messageCount": int(r[1] or 0),
                        "lastId": int(r[2] or 0),
                    }
                    for r in rows
                ]

    def clear_history(self, url, session_id=None):
        with self._lock:
            with self._connect() as conn:
                if session_id:
                    conn.execute("DELETE FROM messages WHERE notebook_url = ? AND session_id = ?", (url, str(session_id)))
                else:
                    conn.execute("DELETE FROM messages WHERE notebook_url = ?", (url,))
                conn.commit()

    def upsert_fact(self, url, key, value):
        u = str(url or "").strip()
        k = str(key or "").strip()
        v = str(value or "").strip()
        if not u or not k or not v:
            return
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO profile_facts (notebook_url, fact_key, fact_value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(notebook_url, fact_key)
                    DO UPDATE SET fact_value = excluded.fact_value, updated_at = excluded.updated_at
                    """,
                    (u, k, v, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()

    def get_facts(self, url, limit=MAX_PROFILE_FACTS):
        u = str(url or "").strip()
        if not u:
            return {}
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT fact_key, fact_value
                    FROM profile_facts
                    WHERE notebook_url = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (u, int(limit)),
                ).fetchall()
                out = {}
                for k, v in rows:
                    if k and v and k not in out:
                        out[str(k)] = str(v)
                return out

dep_manager = DependencyManager(SCRAPED_DIR)
try:
    from .memory import memory_store
except Exception:
    memory_store = None

if memory_store is None:
    try:
        memory_store = LocalMemoryStore(CHAT_MEMORY_DB)
    except Exception as e:
        log(f"Failed to initialize LocalMemoryStore: {e}")

def main():
    global _LAST_NOTEBOOK_TAB_ID, _LAST_NOTEBOOK_URL
    log("=== Structured Scraper + AI Host started ===")
    while True:
        msg = read_msg()
        if msg is None:
            log("Chrome disconnected.")
            break

        m_type = msg.get("type")
        log(f"Received message type: {m_type}")
        
        if m_type == "CHAT_REQUEST":
            url = _history_url_key(msg.get("url"))
            prompt = str(msg.get("prompt", ""))
            tab_id = msg.get("tabId")
            session_id = str(msg.get("sessionId") or "default")
            if not url:
                send_msg({"type": "CHAT_RESPONSE", "error": "Missing or invalid notebook URL.", "tabId": tab_id})
                continue
            
            # 1. Context
            context = ""
            builder = dep_manager.get_builder(url)
            mode = "simple" # Default
            
            if builder:
                cell_num = _extract_cell_number(prompt)
                if cell_num is not None:
                    context = builder.get_cell_context(cell_num)
                    mode = "dependency"

            # Persist lightweight profile facts (e.g., name) for stable recall.
            extracted_facts = _extract_user_profile_facts(prompt)
            for fact_key, fact_value in extracted_facts.items():
                memory_store.upsert_fact(url, fact_key, fact_value)

            facts = memory_store.get_facts(url)
            profile_context = _build_profile_memory_context(facts)

            # Deterministic fallback for critical identity recall.
            if re.search(r"\b(what\s+is|tell\s+me)\s+my\s+name\b", prompt, re.IGNORECASE):
                known_name = (facts.get("name") or "").strip()
                if known_name:
                    history = memory_store.get_history(url, session_id=session_id)
                    memory_store.append(url, "user", prompt, session_id=session_id)
                    response = f"Your name is {known_name}."
                    memory_store.append(url, "assistant", response, session_id=session_id)
                    send_msg({"type": "CHAT_RESPONSE", "response": response, "tabId": tab_id, "url": url, "sessionId": session_id})
                    continue

            if profile_context:
                if context:
                    context = f"{profile_context}\n\n{context}"
                else:
                    context = profile_context

            # Keep prompt+history dominant in normal chat; large notebook dumps hurt recall.
            if context and len(context) > MAX_CONTEXT_CHARS:
                context = context[:MAX_CONTEXT_CHARS]

            # 2. History
            history = memory_store.get_history(url, session_id=session_id)
            memory_store.append(url, "user", prompt, session_id=session_id)

            # Cancel any active stream on this tab before starting the next one.
            active_key = str(tab_id)
            with _ACTIVE_STREAMS_LOCK:
                prev = _ACTIVE_STREAMS.get(active_key)
                _stop_active_stream(prev)
            if prev and prev.get("sessionId"):
                _signal_remote_stop(prev.get("sessionId"))

            worker = threading.Thread(
                target=_run_streaming_chat,
                args=(url, prompt, tab_id, session_id, history, context, mode),
                daemon=True,
            )
            with _ACTIVE_STREAMS_LOCK:
                _ACTIVE_STREAMS[active_key] = {
                    "thread": worker,
                    "sessionId": session_id,
                    "stopped": False,
                    "url": url,
                }
            worker.start()
            continue

        if m_type == "STOP_CHAT":
            tab_id = msg.get("tabId")
            active_key = str(tab_id)
            session_id = str(msg.get("sessionId") or "")
            with _ACTIVE_STREAMS_LOCK:
                state = _ACTIVE_STREAMS.get(active_key)
                if state:
                    _stop_active_stream(state)
                    if not session_id:
                        session_id = str(state.get("sessionId") or "")
            _signal_remote_stop(session_id)
            send_msg({"type": "CHAT_STREAM_END", "stopped": True, "tabId": tab_id, "url": _history_url_key(msg.get("url")), "sessionId": session_id})
            continue

        if m_type == "PROMPT_SIGNAL":
            cell_index = msg.get("cellIndex")
            exec_order = msg.get("execOrder")
            text = str(msg.get("text") or "").strip()
            tab_url = _normalized_url(msg.get("tabUrl") or "")
            exec_ts = msg.get("ts")  # Timestamp from extension
            print(f"[RECV-SIGNAL] cell={cell_index}, order={exec_order}, ts={exec_ts}, text='{text}'")
            log(f"PROMPT_SIGNAL cell={cell_index if cell_index is not None else '?'} order={exec_order} text={text} ts={exec_ts}")
            
            # Trigger cell execution update with timestamp
            if cell_index is not None and tab_url:
                import subprocess
                script_path = Path(__file__).parent / "update_cell_execution.py"
                try:
                    args = [sys.executable, str(script_path), str(cell_index), tab_url]
                    if exec_ts is not None or exec_order is not None:
                        args.append(str(exec_ts) if exec_ts is not None else "None")
                    if exec_order is not None:
                        args.append(str(exec_order))
                    subprocess.Popen(args)
                except Exception:
                    pass
            
            send_msg({
                "ok": True,
                "type": "PROMPT_SIGNAL",
                "cellIndex": cell_index,
                "tabUrl": tab_url,
            })
            continue

        def push_graph(url, tid):
            if not tid:
                return
            try:
                b = dep_manager.get_builder(url)
                if b:
                    t = b.tracker
                    gd = []
                    for num, data in b.cells.items():
                        gd.append({
                            'cell_number': num,
                            'input_preview': data.get('code', '')[:120],
                            'dependencies': t.get_dependencies(num, transitive=False),
                            'reverse_dependencies': t.get_reverse_dependencies(num)
                        })
                    send_msg({"type": "GRAPH_DATA", "graph": gd, "tabId": tid, "url": url})
                    return

                fallback_graph = _build_fallback_graph(url)
                if fallback_graph is not None:
                    note = None
                    send_msg({"type": "GRAPH_DATA", "graph": fallback_graph, "tabId": tid, "error": note, "url": url})
                    return

                send_msg({"type": "GRAPH_DATA", "graph": [], "tabId": tid, "error": "No notebook data available yet for this page.", "url": url})
            except Exception as e:
                log(f"Push Graph Error: {e}")
                send_msg({"type": "GRAPH_DATA", "graph": [], "tabId": tid, "error": f"Graph generation failed: {e}", "url": url})

        if m_type == "GET_GRAPH":
            url = _normalized_url(msg.get("url") or "")
            tab_id = msg.get("tabId")
            push_graph(url, tab_id)
            continue

        if m_type == "GET_HISTORY":
            url = _history_url_key(msg.get("url"))
            tab_id = msg.get("tabId")
            session_id = str(msg.get("sessionId") or "default")
            history = memory_store.get_history(url, session_id=session_id) if url else []
            sessions = memory_store.list_sessions(url) if url else []
            send_msg({
                "type": "HISTORY_DATA",
                "history": history,
                "sessions": sessions,
                "activeSessionId": session_id,
                "tabId": tab_id,
                "url": url,
            })
            continue

        if m_type == "CLEAR_HISTORY":
            url = _history_url_key(msg.get("url"))
            tab_id = msg.get("tabId")
            session_id = msg.get("sessionId")
            if url:
                memory_store.clear_history(url, session_id=session_id)
            send_msg({"type": "HISTORY_CLEARED", "url": url, "tabId": tab_id, "sessionId": session_id})
            continue

        if m_type in {"CLICK_CELL_RESULT", "CLICK_CELL_ERROR", "CLICK_SELECTOR_RESULT", "CLICK_SELECTOR_ERROR", "SELECT_CELL_RESULT", "SELECT_CELL_ERROR", "INSERT_CELL_RESULT", "INSERT_CELL_ERROR", "SEND_KEY_RESULT", "SEND_KEY_ERROR"}:
            _append_jsonl(BOT_RESULTS_PATH, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": m_type,
                "tabId": msg.get("tabId"),
                "url": msg.get("url"),
                "cellIndex": msg.get("cellIndex"),
                "selector": msg.get("selector"),
                "key": msg.get("key"),
                "direction": msg.get("direction"),
                "requestId": msg.get("requestId"),
                "result": msg.get("result"),
            })
            log(f"Bot result {m_type} tabId={msg.get('tabId')} cellIndex={msg.get('cellIndex')} selector={msg.get('selector')} key={msg.get('key')} direction={msg.get('direction')}")
            continue

        if m_type == "NOTEBOOK_DATA":
            tab_url = _normalized_url(msg.get("tabUrl") or "unknown")
            tab_id = msg.get("tabId")
            if isinstance(tab_id, int):
                with _BOT_STATE_LOCK:
                    _LAST_NOTEBOOK_TAB_ID = tab_id
                    _LAST_NOTEBOOK_URL = tab_url
            kernel_status = msg.get("kernelStatus")
            kernel_scenario = msg.get("kernelScenario", "unknown")
            kernel_state = msg.get("kernelState", {})
            kernel_active = _kernel_is_active(kernel_status)
            kernel_scenario_norm = _normalize_kernel_scenario(kernel_scenario)
            
            # Log scenario for debugging
            log(f"[TAB {tab_id}] Kernel Scenario: {kernel_scenario} | Status: {kernel_status}")
            if isinstance(kernel_state, dict):
                log(f"[TAB {tab_id}]   Editor Loading: {kernel_state.get('editorLoading')}, Off: {kernel_state.get('off')}, HDD: {kernel_state.get('hdd')}")
            raw_cells = msg.get("cells", [])
            if not isinstance(raw_cells, list):
                raw_cells = []
            
            code_cells = []
            all_cells = []  # All cells (code + markdown)
            live_cells = []  # Immediate scraped snapshot for live JSON
            for i, cell in enumerate(raw_cells):
                cell_type = cell.get("type", "code")
                # Prefer the authoritative index sent by the extension (data-windowed-list-index)
                try:
                    cell_index = int(cell.get("index")) if cell.get("index") is not None else i + 1
                except Exception:
                    cell_index = i + 1
                
                if cell_type == "code":
                    execution_order = cell.get("execution_order")
                    try:
                        if execution_order is not None:
                            execution_order = int(execution_order)
                    except Exception:
                        execution_order = None
                    code_cell = {
                        "index": cell_index,
                        "input": str(cell.get("source") or cell.get("input") or ""),
                        "output": str(cell.get("output") or ""),
                        "execution_order": execution_order,
                        "execution_title": str(cell.get("execution_title") or "").strip(),
                        "execution_status": str(cell.get("execution_status") or "idle"),
                    }
                    code_cells.append(code_cell)
                    all_cells.append((cell_index, cell_type, code_cell))
                    live_cells.append({
                        "type": "code",
                        "index": cell_index,
                        "input": code_cell["input"],
                        "output": code_cell["output"],
                        "execution_order": execution_order,
                        "execution_title": str(cell.get("execution_title") or "").strip(),
                    })
                elif cell_type == "markdown":
                    # Markdown cells: type, input, index, state
                    markdown_cell = {
                        "type": "markdown",
                        "index": cell_index,
                        "input": str(cell.get("input") or ""),
                        "state": str(cell.get("state") or "open"),  # open or collapsed
                    }
                    all_cells.append((cell_index, cell_type, markdown_cell))
                    live_cells.append(markdown_cell)

            now_iso = datetime.now(timezone.utc).isoformat()
            live_cells.sort(key=lambda cell: int(cell.get("index", 0)))
            live_data = {
                "tabUrl": tab_url,
                "title": str(msg.get("title", "notebook")),
                "lastUpdated": now_iso,
                "cells": live_cells,
            }
            
            import hashlib
            data_str = json.dumps(
                [
                    {
                        "index": cell["index"],
                        "input": cell["input"],
                        "output": cell["output"],
                        "execution_order": cell["execution_order"],
                        "execution_status": cell["execution_status"],
                    }
                    for cell in code_cells
                ],
                sort_keys=True,
            ).encode("utf-8")
            data_hash = hashlib.sha256(data_str).hexdigest()

            should_save = False
            save_cells = []

            with _HASHES_LOCK:
                stored_hashes = _load_hashes()
                if stored_hashes.get(tab_url) != data_hash:
                    should_save = True

            # If persistent JSON file is missing from disk, always allow saving.
            persistent_path = _persistent_dir() / get_safe_filename(tab_url)
            if not persistent_path.exists():
                should_save = True

            with _EXECUTION_STATE_LOCK:
                execution_state = _load_execution_state()
                notebook_state = execution_state.get(tab_url)
                if not isinstance(notebook_state, dict) or "revisions" not in notebook_state:
                    notebook_state = {"active_revision": data_hash, "revisions": {}, "last_seen_at": now_iso, "kernel_active": kernel_active}
                    should_save = True

                previous_kernel_scenario = str(notebook_state.get("last_kernel_scenario") or "").strip().lower()
                scenario_entered = kernel_scenario_norm != previous_kernel_scenario

                # --- Kernel session metadata ---
                if _scenario_is_fresh(kernel_scenario_norm) and scenario_entered:
                    notebook_state["kernel_session_started_at"] = now_iso
                    notebook_state.pop("kernel_session_stopped_at", None)
                    log(f"[Session] Kernel session STARTED at {now_iso}")
                    # Clear persistent execution metadata immediately on fresh kernel start
                    try:
                        ppath = _persistent_dir() / get_safe_filename(tab_url)
                        if ppath.exists():
                            pdata = json.loads(ppath.read_text(encoding="utf-8")) if ppath.exists() else {}
                            pcells = pdata.get("cells", []) if isinstance(pdata, dict) else []
                            for pc in pcells:
                                if isinstance(pc, dict):
                                    pc["execution_order"] = None
                                    pc["execution_title"] = ""
                                    if "execution_timestamp" in pc:
                                        try:
                                            del pc["execution_timestamp"]
                                        except Exception:
                                            pass
                            pdata["lastUpdated"] = now_iso
                            _atomic_write_json(ppath, pdata)
                            log(f"[Fresh] Cleared persistent execution metadata for {tab_url}")
                    except Exception as e:
                        log(f"[Fresh] Failed clearing persistent metadata: {e}")
                elif _scenario_is_off(kernel_scenario_norm) and scenario_entered:
                    notebook_state["kernel_session_stopped_at"] = now_iso
                    log(f"[Session] Kernel session STOPPED at {now_iso}")

                revisions = notebook_state.get("revisions", {})
                if not isinstance(revisions, dict):
                    revisions = {}

                revision_state = revisions.get(data_hash)
                first_fetch = not isinstance(revision_state, dict)
                if first_fetch:
                    revision_state = {"cells": {}, "initialized_at": now_iso, "last_seen_at": now_iso, "kernel_active": kernel_active, "kernel_scenario": kernel_scenario_norm}
                    should_save = True

                # On a reload with active kernel, the hash changes because the DOM is in flux
                # (outputs not yet re-rendered). Bootstrap the new revision from the most recent
                # prior revision so baseline_order/seen_running/title are not lost.
                if first_fetch and _scenario_is_reload(kernel_scenario_norm):
                    best_cells = {}
                    best_ts = ""
                    for rev_data in revisions.values():
                        if isinstance(rev_data, dict):
                            ts = str(rev_data.get("last_seen_at") or "")
                            if ts > best_ts:
                                best_ts = ts
                                best_cells = rev_data.get("cells", {})
                    if best_cells and isinstance(best_cells, dict):
                        seeded = {k: dict(v) for k, v in best_cells.items() if isinstance(v, dict)}
                        revision_state["cells"] = seeded
                        log(f"[Reload] Seeded new revision from prior revision ({len(seeded)} cells)")

                previous_cells = revision_state.get("cells", {})
                if not isinstance(previous_cells, dict):
                    previous_cells = {}

                if _scenario_is_fresh(kernel_scenario_norm) and scenario_entered:
                    previous_cells = {}
                    revision_state["cells"] = {}
                    should_save = True

                updated_cells = {}
                for cell in code_cells:
                    cell_key = str(cell["index"])
                    previous_cell = previous_cells.get(cell_key, {})
                    if not isinstance(previous_cell, dict):
                        previous_cell = {}

                    baseline_order = previous_cell.get("baseline_order")
                    seen_running = bool(previous_cell.get("seen_running"))
                    previous_title = str(previous_cell.get("title") or "")
                    current_order = cell.get("execution_order")
                    current_title = str(cell.get("execution_title") or "").strip()
                    execution_status = str(cell.get("execution_status") or "idle")
                    is_active = execution_status in {"queued", "running"}
                    is_executed = execution_status == "executed"

                    if _scenario_is_fresh(kernel_scenario_norm) and scenario_entered:
                        saved_order = None
                        saved_title = ""
                    elif is_active:
                        saved_order = current_order if current_order is not None else baseline_order
                        if current_order is not None and current_order != baseline_order:
                            saved_title = "Cell is running (Execution #" + str(current_order) + ")"
                        else:
                            saved_title = current_title or "Cell is running"
                        seen_running = True
                        if current_order is not None:
                            should_save = True
                    elif is_executed and current_order is not None:
                        # Fresh kernel: block stale DOM numbers until cell is seen running.
                        if _scenario_is_fresh(kernel_scenario_norm) and not seen_running:
                            saved_order = None
                            saved_title = ""
                        # Reload: DOM numbers are from the prior session — preserve existing
                        # titles, just baseline the order so future real executions are detected.
                        elif _scenario_is_reload(kernel_scenario_norm) and not seen_running:
                            saved_order = current_order
                            saved_title = previous_title or ""
                            baseline_order = current_order
                        elif current_order != baseline_order:
                            saved_order = current_order
                            saved_title = current_title or previous_title or ""
                            baseline_order = current_order
                            should_save = True
                            log(f"EXEC DETECTED cell={cell_key} order={current_order}")
                        else:
                            saved_order = baseline_order
                            saved_title = current_title or previous_title or ""
                    elif current_order is not None and seen_running:
                        if current_order == baseline_order:
                            saved_order = baseline_order
                            saved_title = current_title or previous_title or ""
                        else:
                            saved_order = current_order
                            saved_title = current_title or previous_title or ""
                            baseline_order = current_order
                    else:
                        # current_order is None, OR current_order is set but cell was never seen
                        # running in this session — stale DOM numbers must not assign execution data.
                        saved_order = baseline_order if (_scenario_is_reload(kernel_scenario_norm) or _scenario_is_off(kernel_scenario_norm)) else None
                        if _scenario_is_off(kernel_scenario_norm):
                            saved_title = current_title or previous_title or ""
                        else:
                            saved_title = previous_title or ""

                    updated_cells[cell_key] = {
                        "baseline_order": baseline_order,
                        "seen_running": seen_running,
                        "title": saved_title,
                    }
                    save_cells.append({
                        "type": "code",
                        "index": cell["index"],
                        "input": cell["input"],
                        "output": cell["output"],
                        "execution_order": saved_order,
                        "execution_title": saved_title,
                    })
                
                # Add markdown cells to save_cells (no execution metadata)
                for cell_index, cell_type, cell_data in all_cells:
                    if cell_type == "markdown":
                        save_cells.append(cell_data)

                revision_state["cells"] = updated_cells
                revision_state["last_seen_at"] = now_iso
                # Track how many times we've observed this revision (helps filter transient reloads)
                revision_state["seen_count"] = int(revision_state.get("seen_count") or 0) + 1
                revision_state["kernel_active"] = kernel_active
                revision_state["kernel_scenario"] = kernel_scenario_norm
                revisions[data_hash] = revision_state
                notebook_state["revisions"] = revisions
                notebook_state["active_revision"] = data_hash
                notebook_state["last_seen_at"] = now_iso
                notebook_state["kernel_active"] = kernel_active
                notebook_state["last_kernel_scenario"] = kernel_scenario_norm
                execution_state[tab_url] = notebook_state
                _save_execution_state(execution_state)

            # Existing persistent file path (we compare against persistent copy)
            existing_path = _persistent_dir() / get_safe_filename(tab_url)
            existing_by_index = {}
            if existing_path.is_file():
                try:
                    existing_data = json.loads(existing_path.read_text(encoding="utf-8"))
                    existing_cells = existing_data.get("cells", []) if isinstance(existing_data, dict) else []
                    existing_by_index = {
                        str(cell.get("index")): cell
                        for cell in existing_cells
                        if isinstance(cell, dict) and cell.get("index") is not None
                    }
                    for cell in save_cells:
                        prev_cell = existing_by_index.get(str(cell["index"]), {})
                        # Only compare execution metadata for code cells (markdown cells don't have it)
                        if cell.get("type") != "markdown":
                            if (
                                str(prev_cell.get("execution_order")) != str(cell.get("execution_order"))
                                or str(prev_cell.get("execution_title") or "") != str(cell.get("execution_title") or "")
                            ):
                                should_save = True
                                break
                except Exception:
                    should_save = True

            if should_save:
                # STRICT PRESERVATION: Target fields are owned by update_cell_execution.py.
                # The polling loop must never wipe or overwrite established execution data.
                if existing_by_index:
                    for cell in save_cells:
                        # Skip merge logic for markdown cells (they don't have execution metadata)
                        if cell.get("type") == "markdown":
                            continue
                        
                        prev_cell = existing_by_index.get(str(cell["index"]), {})
                        if not isinstance(prev_cell, dict):
                            continue

                        prev_order = prev_cell.get("execution_order")
                        prev_title = str(prev_cell.get("execution_title") or "")
                        inc_order = cell.get("execution_order")
                        inc_title = str(cell.get("execution_title") or "").strip()

                        # Normalize legacy default — treat as empty.
                        if prev_title == "Cell is not executed yet":
                            prev_title = ""

                        # Merge policy:
                        # - If no prior stored order -> accept incoming as-is.
                        # - If prior exists and incoming is missing -> preserve prior (user turned kernel off or no new info).
                        # - If prior exists and incoming exists:
                        #     * fresh kernel: accept incoming
                        #     * kernel off: preserve prior
                        #     * reload running: accept incoming only if it indicates newer execution (inc_order > prev_order)
                        if prev_order is None:
                            # No prior data; accept incoming values (inc may be None -> remain empty)
                            pass
                        else:
                            if inc_order is None:
                                # No new info: preserve stored values
                                cell["execution_order"] = prev_order
                                cell["execution_title"] = prev_title
                            else:
                                # Incoming has an order
                                if _scenario_is_fresh(kernel_scenario_norm):
                                    # Fresh kernel: accept incoming (baseline reset already handled above)
                                    pass
                                elif _scenario_is_off(kernel_scenario_norm):
                                    # Kernel is off: keep previous values until kernel starts
                                    cell["execution_order"] = prev_order
                                    cell["execution_title"] = prev_title
                                elif _scenario_is_reload(kernel_scenario_norm):
                                    # Reload: only accept if incoming indicates newer execution
                                    try:
                                        if int(inc_order) > int(prev_order):
                                            pass
                                        else:
                                            cell["execution_order"] = prev_order
                                            cell["execution_title"] = prev_title
                                    except Exception:
                                        cell["execution_order"] = prev_order
                                        cell["execution_title"] = prev_title
                # Post-merge sanitization: ensure titles exist and strip timestamps (code cells only)
                for c in save_cells:
                    # Skip sanitization for markdown cells
                    if c.get("type") == "markdown":
                        continue
                    
                    # Remove any execution_timestamp if present (do not persist timestamps)
                    if "execution_timestamp" in c:
                        try:
                            del c["execution_timestamp"]
                        except Exception:
                            pass

                    # Ensure a readable execution_title when we have an order
                    order = c.get("execution_order")
                    title = str(c.get("execution_title") or "").strip()
                    if order is not None and not title:
                        try:
                            c["execution_title"] = f"Execution #{int(order)}"
                        except Exception:
                            c["execution_title"] = f"Execution"

                # Sort cells by index
                save_cells.sort(key=lambda cell: int(cell.get("index", 0)))

                final_data = {
                    "tabUrl": tab_url,
                    "title": str(msg.get("title", "notebook")),
                    "lastUpdated": now_iso,
                    "cells": save_cells,
                }

                # Always write a live snapshot (reflects immediate scraped state)
                save_live_json(live_data, tab_url)

                # Persist only when revision appears stable (seen_count >= 1) or the persistent
                # file doesn't yet exist. This prevents transient reloads from overwriting
                # the stable persistent metadata.
                rev_state = revisions.get(data_hash, {})
                seen_count = int(rev_state.get("seen_count") or 0)
                persistent_path = _persistent_dir() / get_safe_filename(tab_url)
                should_write_persistent = False
                if not persistent_path.exists():
                    should_write_persistent = True
                elif seen_count >= 1:
                    should_write_persistent = True

                # Also persist if execution_order or cell contents changed compared to existing persistent file
                if not should_write_persistent and persistent_path.exists():
                    try:
                        with persistent_path.open('r', encoding='utf-8') as f:
                            existing = json.load(f)
                        existing_by_idx = {int(c.get('index', 0)): c for c in existing.get('cells', []) if isinstance(c, dict)}
                        incoming_cells = final_data.get('cells', [])
                        if incoming_cells and len(incoming_cells) > 0:
                            if len(existing_by_idx) != len(incoming_cells):
                                should_write_persistent = True
                            else:
                                for c in incoming_cells:
                                    idx = int(c.get('index', 0))
                                    prev = existing_by_idx.get(idx, {})
                                    if (
                                        prev.get('type') != c.get('type')
                                        or prev.get('input') != c.get('input')
                                        or prev.get('output') != c.get('output')
                                        or str(prev.get('execution_order')) != str(c.get('execution_order'))
                                    ):
                                        should_write_persistent = True
                                        break
                    except Exception:
                        # On any error, fall back to existing gating logic
                        pass

                if should_write_persistent:
                    save_persistent_json(final_data, tab_url)
                    with _HASHES_LOCK:
                        stored_hashes = _load_hashes()
                        stored_hashes[tab_url] = data_hash
                        _save_hashes(stored_hashes)

                push_graph(tab_url, tab_id)
        
        send_msg({"ok": True})

def initialize():
    """Prepare all subdirectories in the data folder."""
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    CHAT_MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RATE_LIMIT_TRACKER.parent.mkdir(parents=True, exist_ok=True)
    BOT_COMMANDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOT_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOT_COMMANDS_PATH.touch(exist_ok=True)
    BOT_RESULTS_PATH.touch(exist_ok=True)
    _start_bot_command_watcher()
    if _DEP_FALLBACK:
        log("Dependency modules not found; using built-in fallback dependency engine.")
    elif not _DEP_AVAILABLE:
        log("Dependency mode modules not found; dependency graph features disabled.")
    log("=== Local Data Registry Organized ===")

def entry_point():
    """Run the scraper and handle graceful shutdown on interrupt or error."""
    try:
        initialize()
        main()
    except KeyboardInterrupt:
        log("Interrupted by user – shutting down.")
    except Exception as e:
        log(f"Unexpected error: {e}")
        raise

if __name__ == "__main__":
    entry_point()
