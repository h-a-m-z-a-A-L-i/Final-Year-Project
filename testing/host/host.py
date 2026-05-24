import json, struct, sys, threading, re, sqlite3, os, ast, uuid
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse
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
    from .prompt_utils import _extract_cell_number, _extract_user_profile_facts, _build_profile_memory_context
    from .notebook_data_handler import build_graph_payload, handle_get_graph, handle_notebook_data, _normalized_url
    from .persistence_helpers import _atomic_write_json, save_json, save_live_json, save_persistent_json, get_safe_filename, _load_hashes, _save_hashes, _load_execution_state, _save_execution_state
    from .memory import memory_store
    from .streaming import _run_streaming_chat, _signal_remote_stop, _stop_active_stream
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
        from prompt_utils import _extract_cell_number, _extract_user_profile_facts, _build_profile_memory_context
        from notebook_data_handler import build_graph_payload, handle_get_graph, handle_notebook_data, _normalized_url
        from persistence_helpers import _atomic_write_json, save_json, save_live_json, save_persistent_json, get_safe_filename, _load_hashes, _save_hashes, _load_execution_state, _save_execution_state
        from memory import memory_store
        from streaming import _run_streaming_chat, _signal_remote_stop, _stop_active_stream
    except Exception:
        # As a final fallback try package-style import using repo folder name
        from testing.host.config import *
        from testing.host.config import _HASHES_LOCK, _EXECUTION_STATE_LOCK, _SEND_LOCK, _ACTIVE_STREAMS, _ACTIVE_STREAMS_LOCK, _RATE_LOCK, _BOT_STATE_LOCK, _CEREBRAS_CLIENT
        from testing.host.dispatcher import read_msg, send_msg, _append_jsonl, _start_bot_command_watcher
        from testing.host.prompt_utils import _extract_cell_number, _extract_user_profile_facts, _build_profile_memory_context
        from testing.host.notebook_data_handler import build_graph_payload, handle_get_graph, handle_notebook_data, _normalized_url
        from testing.host.persistence_helpers import _atomic_write_json, save_json, save_live_json, save_persistent_json, get_safe_filename, _load_hashes, _save_hashes, _load_execution_state, _save_execution_state
        from testing.host.memory import memory_store
        from testing.host.streaming import _run_streaming_chat, _signal_remote_stop, _stop_active_stream

# configuration comes from testing/host/config.py

def _history_url_key(url: str) -> str:
    return _normalized_url(url or "")

try:
    from .dependency import _build_fallback_graph, DependencyManager, _DEP_AVAILABLE, _DEP_FALLBACK
except Exception:
    try:
        from dependency import _build_fallback_graph, DependencyManager, _DEP_AVAILABLE, _DEP_FALLBACK
    except Exception:
        from testing.host.dependency import _build_fallback_graph, DependencyManager, _DEP_AVAILABLE, _DEP_FALLBACK

dep_manager = DependencyManager(SCRAPED_DIR)

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8080
_HTTP_SERVER = None
_HTTP_THREAD = None


def _start_http_server():
    global _HTTP_SERVER, _HTTP_THREAD
    if _HTTP_SERVER is not None:
        return

    class GraphRequestHandler(BaseHTTPRequestHandler):
        def _send_json(self, status_code: int, payload: dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/graph":
                self.send_error(404, "Not Found")
                return

            query = parse_qs(parsed.query or "")
            url = _normalized_url((query.get("url") or [""])[0])
            try:
                payload = build_graph_payload({"dep_manager": dep_manager, "log": log}, url)
                graph = payload.get("graph", []) if isinstance(payload, dict) else []
                response = {
                    "url": url,
                    "graph": graph,
                    "cells": graph,
                    "error": payload.get("error") if isinstance(payload, dict) else None,
                }
                self._send_json(200, response)
            except Exception as exc:
                self._send_json(500, {"url": url, "graph": [], "cells": [], "error": str(exc)})

        def log_message(self, format, *args):
            return

    try:
        _HTTP_SERVER = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), GraphRequestHandler)
        _HTTP_SERVER.daemon_threads = True

        def _serve():
            try:
                _HTTP_SERVER.serve_forever(poll_interval=0.25)
            except Exception as exc:
                log(f"HTTP server stopped: {exc}")

        _HTTP_THREAD = threading.Thread(target=_serve, daemon=True)
        _HTTP_THREAD.start()
        log(f"HTTP graph endpoint listening on http://{HTTP_HOST}:{HTTP_PORT}/graph")
    except OSError as exc:
        log(f"HTTP graph endpoint unavailable on {HTTP_HOST}:{HTTP_PORT}: {exc}")

def main():
    log("=== Structured Scraper + AI Host started ===")
    host_ctx = {
        "dep_manager": dep_manager,
        "send_msg": send_msg,
        "log": log,
        "bot_state": {"tabId": None, "url": None},
        "bot_state_lock": _BOT_STATE_LOCK,
    }

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

            context = ""
            builder = dep_manager.get_builder(url)
            mode = "simple"
            if builder:
                cell_num = _extract_cell_number(prompt)
                if cell_num is not None:
                    context = builder.get_cell_context(cell_num)
                    mode = "dependency"

            extracted_facts = _extract_user_profile_facts(prompt)
            for fact_key, fact_value in extracted_facts.items():
                memory_store.upsert_fact(url, fact_key, fact_value)

            facts = memory_store.get_facts(url)
            profile_context = _build_profile_memory_context(facts)

            if re.search(r"\b(what\s+is|tell\s+me)\s+my\s+name\b", prompt, re.IGNORECASE):
                known_name = (facts.get("name") or "").strip()
                if known_name:
                    memory_store.append(url, "user", prompt, session_id=session_id)
                    response = f"Your name is {known_name}."
                    memory_store.append(url, "assistant", response, session_id=session_id)
                    send_msg({"type": "CHAT_RESPONSE", "response": response, "tabId": tab_id, "url": url, "sessionId": session_id})
                    continue

            if profile_context:
                context = f"{profile_context}\n\n{context}" if context else profile_context
            if context and len(context) > MAX_CONTEXT_CHARS:
                context = context[:MAX_CONTEXT_CHARS]

            history = memory_store.get_history(url, session_id=session_id)
            memory_store.append(url, "user", prompt, session_id=session_id)

            active_key = str(tab_id)
            with _ACTIVE_STREAMS_LOCK:
                prev = _ACTIVE_STREAMS.get(active_key)
                _stop_active_stream(prev)
            if prev and prev.get("sessionId"):
                _signal_remote_stop(prev.get("sessionId"))

            worker = threading.Thread(target=_run_streaming_chat, args=(url, prompt, tab_id, session_id, history, context, mode), daemon=True)
            with _ACTIVE_STREAMS_LOCK:
                _ACTIVE_STREAMS[active_key] = {"thread": worker, "sessionId": session_id, "stopped": False, "url": url}
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
            exec_ts = msg.get("ts")
            print(f"[RECV-SIGNAL] cell={cell_index}, order={exec_order}, ts={exec_ts}, text='{text}'")
            log(f"PROMPT_SIGNAL cell={cell_index if cell_index is not None else '?'} order={exec_order} text={text} ts={exec_ts}")
            if cell_index is not None and tab_url:
                try:
                    try:
                        from update_cell_execution import update_cell_execution
                    except Exception:
                        from testing.host.update_cell_execution import update_cell_execution

                    def run_update():
                        try:
                            update_cell_execution(cell_index, tab_url, exec_ts, exec_order)
                        except Exception as e:
                            log(f"Error in update_cell_execution background thread: {e}")

                    threading.Thread(target=run_update, daemon=True).start()
                except Exception:
                    pass
            send_msg({"ok": True, "type": "PROMPT_SIGNAL", "cellIndex": cell_index, "tabUrl": tab_url})
            continue

        if m_type == "GET_GRAPH":
            handle_get_graph(host_ctx, msg)
            continue

        if m_type == "GET_HISTORY":
            url = _history_url_key(msg.get("url"))
            tab_id = msg.get("tabId")
            session_id = str(msg.get("sessionId") or "default")
            history = memory_store.get_history(url, session_id=session_id) if url else []
            sessions = memory_store.list_sessions(url) if url else []
            send_msg({"type": "HISTORY_DATA", "history": history, "sessions": sessions, "activeSessionId": session_id, "tabId": tab_id, "url": url})
            continue

        if m_type == "CLEAR_HISTORY":
            url = _history_url_key(msg.get("url"))
            tab_id = msg.get("tabId")
            session_id = msg.get("sessionId")
            if url:
                memory_store.clear_history(url, session_id=session_id)
            send_msg({"type": "HISTORY_CLEARED", "url": url, "tabId": tab_id, "sessionId": session_id})
            continue

        if m_type in {"CLICK_CELL_RESULT", "CLICK_CELL_ERROR", "CLICK_SELECTOR_RESULT", "CLICK_SELECTOR_ERROR", "SELECT_CELL_RESULT", "SELECT_CELL_ERROR", "INSERT_CELL_RESULT", "INSERT_CELL_ERROR", "SEND_KEY_RESULT", "SEND_KEY_ERROR", "DELETE_CELL_RESULT", "DELETE_CELL_ERROR", "SEND_KEYS_RESULT", "SEND_KEYS_ERROR"}:
            try:
                from .bot_command import complete_bot_result
            except Exception:
                from bot_command import complete_bot_result
            record = complete_bot_result(msg)
            record["diagnostics"] = msg.get("diagnostics")
            if msg.get("tunnel"):
                record["tunnel"] = msg.get("tunnel")
            _append_jsonl(BOT_RESULTS_PATH, record)
            log(
                f"Bot result {m_type} ok={record.get('ok')} tabId={msg.get('tabId')} "
                f"cellIndex={msg.get('cellIndex')} requestId={msg.get('requestId')}"
            )
            continue

        if m_type == "NOTEBOOK_DATA":
            handle_notebook_data(host_ctx, msg)
            continue

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
    _start_http_server()
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
