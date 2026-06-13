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
    from .config import _HASHES_LOCK, _EXECUTION_STATE_LOCK, _SEND_LOCK, _ACTIVE_STREAMS, _ACTIVE_STREAMS_LOCK, _RATE_LOCK, _BOT_STATE_LOCK
    from .dispatcher import get_next_message, send_msg, _append_jsonl, start_host_io
    from .prompt_utils import _extract_cell_number, _extract_user_profile_facts, _build_profile_memory_context
    from .notebook_data_handler import build_graph_payload, handle_get_graph, handle_notebook_data, _normalized_url
    from .persistence_helpers import _atomic_write_json, save_json, save_live_json, save_persistent_json, get_safe_filename, _load_hashes, _save_hashes, _load_execution_state, _save_execution_state
    from .memory import memory_store
    from .streaming import (
        _run_streaming_chat,
        _signal_remote_stop,
        begin_active_stream,
        is_stream_stopped,
        resolve_active_key,
        send_stream_end,
    )
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
        from config import _HASHES_LOCK, _EXECUTION_STATE_LOCK, _SEND_LOCK, _ACTIVE_STREAMS, _ACTIVE_STREAMS_LOCK, _RATE_LOCK, _BOT_STATE_LOCK
        from dispatcher import get_next_message, send_msg, _append_jsonl, start_host_io
        from prompt_utils import _extract_cell_number, _extract_user_profile_facts, _build_profile_memory_context
        from notebook_data_handler import build_graph_payload, handle_get_graph, handle_notebook_data, _normalized_url
        from persistence_helpers import _atomic_write_json, save_json, save_live_json, save_persistent_json, get_safe_filename, _load_hashes, _save_hashes, _load_execution_state, _save_execution_state
        from memory import memory_store
        from streaming import (
            _run_streaming_chat,
            _signal_remote_stop,
            begin_active_stream,
            is_stream_stopped,
            resolve_active_key,
            send_stream_end,
        )
    except Exception:
        # As a final fallback try package-style import using repo folder name
        from testing.host.config import *
        from testing.host.config import _HASHES_LOCK, _EXECUTION_STATE_LOCK, _SEND_LOCK, _ACTIVE_STREAMS, _ACTIVE_STREAMS_LOCK, _RATE_LOCK, _BOT_STATE_LOCK
        from testing.host.dispatcher import get_next_message, send_msg, _append_jsonl, start_host_io
        from testing.host.prompt_utils import _extract_cell_number, _extract_user_profile_facts, _build_profile_memory_context
        from testing.host.notebook_data_handler import build_graph_payload, handle_get_graph, handle_notebook_data, _normalized_url
        from testing.host.persistence_helpers import _atomic_write_json, save_json, save_live_json, save_persistent_json, get_safe_filename, _load_hashes, _save_hashes, _load_execution_state, _save_execution_state
        from testing.host.memory import memory_store
        from testing.host.streaming import (
            _run_streaming_chat,
            _signal_remote_stop,
            begin_active_stream,
            is_stream_stopped,
            resolve_active_key,
            send_stream_end,
        )

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
        msg = get_next_message()
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

            stream_channel = str(msg.get("streamChannel") or "main").strip() or "main"
            active_key = resolve_active_key(tab_id, stream_channel)
            begin_active_stream(active_key, session_id, url)

            try:
                from .prompt_engineering import detect_mode, merge_context_with_profile
                from .notebook_context import pack_context
            except Exception:
                from prompt_engineering import detect_mode, merge_context_with_profile
                from notebook_context import pack_context

            ui_mode = str(msg.get("mode") or "ask").strip().lower()
            raw_cell = msg.get("cellIndex")
            if raw_cell is not None and str(raw_cell).strip() != "":
                try:
                    cell_num = int(raw_cell)
                except (TypeError, ValueError):
                    cell_num = _extract_cell_number(prompt)
            else:
                cell_num = _extract_cell_number(prompt)

            with _BOT_STATE_LOCK:
                bot_state = dict(host_ctx.get("bot_state") or {})
            dep_manager.set_bot_state(bot_state)

            mode = detect_mode(
                prompt,
                ui_mode,
                has_cell_context=cell_num is not None,
            )

            ctx_pack = pack_context(
                mode=mode,
                url=url,
                prompt=prompt,
                cell_index=cell_num,
                dep_manager=dep_manager,
                bot_state=bot_state,
            )
            log(
                f"Context pack mode={mode} coverage={ctx_pack.coverage} "
                f"snapshot={ctx_pack.snapshot} cell={ctx_pack.cell_index}"
            )

            extracted_facts = _extract_user_profile_facts(prompt)
            for fact_key, fact_value in extracted_facts.items():
                memory_store.upsert_fact(url, fact_key, fact_value, session_id=session_id)

            facts = memory_store.get_facts(url, session_id=session_id)
            profile_context = _build_profile_memory_context(facts)

            if re.search(r"\b(what\s+is|tell\s+me)\s+my\s+name\b", prompt, re.IGNORECASE):
                known_name = (facts.get("name") or "").strip()
                if known_name:
                    memory_store.append(url, "user", prompt, session_id=session_id)
                    response = f"Your name is {known_name}."
                    memory_store.append(url, "assistant", response, session_id=session_id)
                    send_msg({"type": "CHAT_RESPONSE", "response": response, "tabId": tab_id, "url": url, "sessionId": session_id})
                    continue

            context = merge_context_with_profile(ctx_pack.text, profile_context)

            if is_stream_stopped(active_key, session_id):
                send_stream_end(url, tab_id, session_id, stopped=True)
                continue

            history = memory_store.get_history(url, session_id=session_id)
            memory_store.append(url, "user", prompt, session_id=session_id)

            if is_stream_stopped(active_key, session_id):
                send_stream_end(url, tab_id, session_id, stopped=True)
                continue

            context_meta = {
                "coverage": ctx_pack.coverage,
                "cell_index": ctx_pack.cell_index,
                "snapshot": ctx_pack.snapshot,
                "active_key": active_key,
                "stream_channel": stream_channel,
            }

            worker = threading.Thread(
                target=_run_streaming_chat,
                args=(url, prompt, tab_id, session_id, history, context, mode, ui_mode, context_meta),
                daemon=True,
            )
            with _ACTIVE_STREAMS_LOCK:
                state = _ACTIVE_STREAMS.get(active_key)
                if state is not None:
                    state["thread"] = worker
            worker.start()
            continue

        if m_type == "STOP_CHAT":
            tab_id = msg.get("tabId")
            session_id = str(msg.get("sessionId") or "")
            url = _history_url_key(msg.get("url"))
            stream_channel = str(msg.get("streamChannel") or "").strip()
            active_key = resolve_active_key(tab_id, stream_channel) if stream_channel else None
            _signal_remote_stop(session_id)
            with _ACTIVE_STREAMS_LOCK:
                state = None
                if active_key:
                    state = _ACTIVE_STREAMS.get(active_key)
                if state is None and session_id:
                    for key, candidate in _ACTIVE_STREAMS.items():
                        if str(candidate.get("sessionId") or "") == session_id:
                            active_key = key
                            state = candidate
                            break
                if state is None and tab_id is not None:
                    state = _ACTIVE_STREAMS.get(str(tab_id))
                if state and not session_id:
                    session_id = str(state.get("sessionId") or "")
                if state and not url:
                    url = state.get("url") or url
            send_stream_end(url or "", tab_id, session_id, stopped=True)
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
            send_msg({
                "type": "HISTORY_DATA",
                "history": history,
                "sessions": sessions,
                "activeSessionId": session_id,
                "sessionId": session_id,
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

        if m_type == "INSERT_CODE_CELL":
            url = _history_url_key(msg.get("url"))
            tab_id = msg.get("tabId")
            request_id = msg.get("requestId")
            try:
                anchor_index = int(msg.get("index"))
            except Exception:
                send_msg({
                    "type": "INSERT_CODE_CELL_RESULT",
                    "ok": False,
                    "error": "index must be an integer (insert new cell below this index)",
                    "tabId": tab_id,
                    "url": url,
                    "requestId": request_id,
                })
                continue
            content = str(msg.get("content") or "")
            if not url:
                send_msg({
                    "type": "INSERT_CODE_CELL_RESULT",
                    "ok": False,
                    "error": "Missing notebook URL",
                    "tabId": tab_id,
                })
                continue
            try:
                from .tool_adapters import insert_and_edit_cell
            except Exception:
                from tool_adapters import insert_and_edit_cell
            log(f"INSERT_CODE_CELL below={anchor_index} url={url} chars={len(content)}")

            def _run_insert_code_cell():
                try:
                    result = insert_and_edit_cell({
                        "url": url,
                        "index": anchor_index,
                        "direction": "below",
                        "content": content,
                        "tab_id": tab_id,
                        "tabId": tab_id,
                        "timeout": 30,
                    })
                    send_msg({
                        "type": "INSERT_CODE_CELL_RESULT",
                        "ok": bool(result.get("ok")),
                        "tabId": tab_id,
                        "url": url,
                        "requestId": request_id,
                        "result": result,
                        "error": None if result.get("ok") else (result.get("error") or "insert failed"),
                    })
                except Exception as exc:
                    log(f"INSERT_CODE_CELL error: {exc}")
                    send_msg({
                        "type": "INSERT_CODE_CELL_RESULT",
                        "ok": False,
                        "tabId": tab_id,
                        "url": url,
                        "requestId": request_id,
                        "error": str(exc),
                    })

            threading.Thread(target=_run_insert_code_cell, daemon=True).start()
            continue

        if m_type == "NOTEBOOK_DATA":
            try:
                handle_notebook_data(host_ctx, msg)
            except Exception as e:
                log(f"NOTEBOOK_DATA error: {e}")
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
    start_host_io()
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
