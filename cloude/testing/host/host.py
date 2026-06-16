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
    from .prompt_utils import (
        _extract_cell_number,
        _extract_user_profile_facts,
        _build_profile_memory_context,
        resolve_cell_index,
    )
    from .notebook_context import load_notebook_snapshot, _cells_from_data
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
        from prompt_utils import (
            _extract_cell_number,
            _extract_user_profile_facts,
            _build_profile_memory_context,
            resolve_cell_index,
        )
        from notebook_context import load_notebook_snapshot, _cells_from_data
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

def _history_url_key(url: str, notebook_id=None) -> str:
    try:
        from .notebook_identity import resolve_history_key
    except Exception:
        from notebook_identity import resolve_history_key
    return resolve_history_key(url, notebook_id, memory_store=memory_store)


def _resolve_chat_key(msg: dict) -> str:
    """Stable chat key: prefer resolved kaggle:kernel:id over URL slug."""
    url = msg.get("url")
    notebook_id = msg.get("notebookId")
    hinted = str(msg.get("notebookKey") or "").strip()
    resolved = _history_url_key(url, notebook_id)
    if str(resolved).startswith("kaggle:kernel:"):
        return resolved
    if hinted.startswith("kaggle:kernel:"):
        return hinted
    return resolved or hinted or _normalized_url(str(url or ""))

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
    try:
        try:
            from .kaggle_kernel_client import scan_import_local_metadata_files
        except Exception:
            from kaggle_kernel_client import scan_import_local_metadata_files
        imported = scan_import_local_metadata_files()
        if imported:
            log(f"[kaggle] Imported {imported} local kernel-metadata.json file(s)")
    except Exception as exc:
        log(f"[kaggle] Local metadata import skipped: {exc}")

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

        if m_type == "RESOLVE_NOTEBOOK_IDENTITY":
            try:
                from .notebook_identity import resolve_notebook_identity
            except Exception:
                from notebook_identity import resolve_notebook_identity
            tab_id = msg.get("tabId")
            identity = resolve_notebook_identity(
                msg.get("url") or "",
                msg.get("notebookId"),
                memory_store=memory_store,
                log=log,
            )
            send_msg({
                "type": "NOTEBOOK_IDENTITY",
                "tabId": tab_id,
                "url": identity.get("url"),
                "notebookId": identity.get("notebookId"),
                "notebookKey": identity.get("notebookKey"),
            })
            continue

        if m_type == "CHAT_REQUEST":
            snapshot_url = _normalized_url(msg.get("url"))
            history_key = _resolve_chat_key(msg)
            prompt = str(msg.get("prompt", ""))
            tab_id = msg.get("tabId")
            session_id = str(msg.get("sessionId") or "default")
            if not isinstance(tab_id, int) or tab_id <= 0:
                send_msg({
                    "type": "CHAT_RESPONSE",
                    "error": "Missing tab context. Reload the notebook page and try again.",
                    "tabId": tab_id,
                    "url": snapshot_url,
                })
                send_msg({
                    "type": "CHAT_STREAM_END",
                    "error": "Missing tab context. Reload the notebook page and try again.",
                    "stopped": False,
                    "tabId": tab_id,
                    "url": snapshot_url,
                    "sessionId": session_id,
                })
                continue
            if not history_key:
                send_msg({"type": "CHAT_RESPONSE", "error": "Missing or invalid notebook URL.", "tabId": tab_id})
                continue

            stream_channel = str(msg.get("streamChannel") or "main").strip() or "main"
            active_key = resolve_active_key(tab_id, stream_channel)
            begin_active_stream(active_key, session_id, history_key)

            try:
                from .prompt_engineering import detect_mode, merge_context_with_profile
                from .notebook_context import pack_context
            except Exception:
                from prompt_engineering import detect_mode, merge_context_with_profile
                from notebook_context import pack_context

            ui_mode = str(msg.get("mode") or "ask").strip().lower()
            try:
                from .agentic_mode import resolve_effective_chat_mode
            except Exception:
                from agentic_mode import resolve_effective_chat_mode
            ui_mode, agentic_warn = resolve_effective_chat_mode(ui_mode)
            if agentic_warn:
                log(agentic_warn)
            raw_cell = msg.get("cellIndex")
            if raw_cell is not None and str(raw_cell).strip() != "":
                try:
                    cell_num = int(raw_cell)
                except (TypeError, ValueError):
                    cell_num = _extract_cell_number(prompt)
            else:
                cell_num = _extract_cell_number(prompt)
            if cell_num is None:
                try:
                    snap, _snap_src = load_notebook_snapshot(snapshot_url)
                    cell_num = resolve_cell_index(prompt, _cells_from_data(snap))
                except Exception:
                    pass

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
                url=snapshot_url,
                prompt=prompt,
                cell_index=cell_num,
                dep_manager=dep_manager,
                bot_state=bot_state,
            )
            log(
                f"Context pack mode={mode} coverage={ctx_pack.coverage} "
                f"snapshot={ctx_pack.snapshot} cell={ctx_pack.cell_index}"
            )

            try:
                from .prompt_cache_baseline import (
                    cerebras_static_cache_enabled,
                    effective_session_id,
                    prepare_static_cache_context,
                )
            except Exception:
                from prompt_cache_baseline import (
                    cerebras_static_cache_enabled,
                    effective_session_id,
                    prepare_static_cache_context,
                )

            memory_session_id = effective_session_id(session_id, mode)
            static_cache = cerebras_static_cache_enabled()

            extracted_facts = _extract_user_profile_facts(prompt)
            for fact_key, fact_value in extracted_facts.items():
                memory_store.upsert_fact(
                    history_key, fact_key, fact_value, session_id=memory_session_id
                )

            facts = memory_store.get_facts(history_key, session_id=memory_session_id)
            profile_context = _build_profile_memory_context(facts)

            if re.search(r"\b(what\s+is|tell\s+me)\s+my\s+name\b", prompt, re.IGNORECASE):
                known_name = (facts.get("name") or "").strip()
                if known_name:
                    memory_store.append(
                        history_key, "user", prompt, session_id=memory_session_id
                    )
                    response = f"Your name is {known_name}."
                    memory_store.append(
                        history_key, "assistant", response, session_id=memory_session_id
                    )
                    send_msg({
                        "type": "CHAT_RESPONSE",
                        "response": response,
                        "tabId": tab_id,
                        "url": snapshot_url,
                        "notebookKey": history_key,
                        "sessionId": session_id,
                    })
                    continue

            turn_tail = ""
            if static_cache:
                baseline_context, turn_tail = prepare_static_cache_context(
                    history_key=history_key,
                    session_id=memory_session_id,
                    mode=mode,
                    url=snapshot_url,
                    profile_context=profile_context,
                )
                context = baseline_context
                pack_coverage = "baseline"
                log(
                    f"Static notebook cache: baseline in system, delta on current turn "
                    f"(session={memory_session_id}, mode={mode})"
                )
            else:
                context = merge_context_with_profile(ctx_pack.text, profile_context)
                pack_coverage = ctx_pack.coverage

            if is_stream_stopped(active_key, session_id):
                send_stream_end(history_key, tab_id, session_id, stopped=True)
                continue

            history = memory_store.get_history(history_key, session_id=memory_session_id)
            memory_store.append(history_key, "user", prompt, session_id=memory_session_id)

            if is_stream_stopped(active_key, session_id):
                send_stream_end(history_key, tab_id, session_id, stopped=True)
                continue

            context_meta = {
                "coverage": pack_coverage,
                "cell_index": cell_num if cell_num is not None else ctx_pack.cell_index,
                "snapshot": ctx_pack.snapshot,
                "active_key": active_key,
                "stream_channel": stream_channel,
                "snapshot_url": snapshot_url,
                "history_key": history_key,
                "agentic_warn": agentic_warn,
                "static_cache": static_cache,
                "turn_tail": turn_tail,
                "cache_session_id": memory_session_id,
            }

            def _stream_worker(*stream_args):
                try:
                    from .notebook_semantic_index import set_active_notebook_key
                except Exception:
                    from notebook_semantic_index import set_active_notebook_key
                meta = stream_args[8] if len(stream_args) > 8 else {}
                key = (meta or {}).get("history_key") or (stream_args[0] if stream_args else "")
                set_active_notebook_key(str(key or ""))
                _run_streaming_chat(*stream_args)

            worker = threading.Thread(
                target=_stream_worker,
                args=(history_key, prompt, tab_id, session_id, history, context, mode, ui_mode, context_meta),
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
            url = _history_url_key(msg.get("url"), msg.get("notebookId"))
            stream_channel = str(msg.get("streamChannel") or "main").strip() or "main"
            active_key = resolve_active_key(tab_id, stream_channel) if tab_id is not None else None
            _signal_remote_stop(session_id, active_key=active_key)
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
                    active_key = str(tab_id)
                    state = _ACTIVE_STREAMS.get(active_key)
                if state is not None:
                    _stop_active_stream(state)
                    if not session_id:
                        session_id = str(state.get("sessionId") or "")
                    if not url:
                        url = state.get("url") or url
                    tab_id = tab_id if tab_id is not None else active_key.split(":", 1)[0]
            log(f"STOP_CHAT session={session_id} active_key={active_key}")
            send_stream_end(url or "", tab_id, session_id, stopped=True, snapshot_url=url)
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
                def run_prompt_signal_updates():
                    try:
                        try:
                            from execution_signal_patch import patch_prompt_execution_signal
                        except Exception:
                            from testing.host.execution_signal_patch import patch_prompt_execution_signal
                        patch_prompt_execution_signal(
                            cell_index,
                            tab_url,
                            text,
                            exec_order=exec_order,
                            exec_ts=exec_ts,
                            log=log,
                        )
                    except Exception as e:
                        log(f"Error in patch_prompt_execution_signal: {e}")
                    try:
                        try:
                            from update_cell_execution import update_cell_execution
                        except Exception:
                            from testing.host.update_cell_execution import update_cell_execution
                        update_cell_execution(cell_index, tab_url, exec_ts, exec_order)
                    except Exception as e:
                        log(f"Error in update_cell_execution background thread: {e}")

                threading.Thread(target=run_prompt_signal_updates, daemon=True).start()
            send_msg({"ok": True, "type": "PROMPT_SIGNAL", "cellIndex": cell_index, "tabUrl": tab_url})
            continue

        if m_type == "NOTEBOOK_URL_CHANGED":
            try:
                from .notebook_identity import handle_notebook_url_changed
            except Exception:
                from notebook_identity import handle_notebook_url_changed
            info = handle_notebook_url_changed(
                msg.get("oldUrl") or "",
                msg.get("newUrl") or "",
                msg.get("notebookId"),
                memory_store=memory_store,
                log=log,
            )
            log(
                f"NOTEBOOK_URL_CHANGED {info.get('url')} key={info.get('notebookKey')} "
                f"migrated={info.get('migratedRows', 0)}"
            )
            continue

        if m_type == "GET_AGENTIC_SETTINGS":
            try:
                from .agentic_mode import get_agentic_settings
            except Exception:
                from agentic_mode import get_agentic_settings
            send_msg({
                "type": "AGENTIC_SETTINGS",
                "tabId": msg.get("tabId"),
                **get_agentic_settings(),
            })
            continue

        if m_type == "SET_AGENTIC_SETTINGS":
            try:
                from .agentic_mode import get_agentic_settings, set_dashboard_agentic_enabled
            except Exception:
                from agentic_mode import get_agentic_settings, set_dashboard_agentic_enabled
            enabled = msg.get("dashboard_enabled")
            if enabled is None:
                enabled = msg.get("enabled")
            set_dashboard_agentic_enabled(bool(enabled))
            send_msg({
                "type": "AGENTIC_SETTINGS",
                "tabId": msg.get("tabId"),
                **get_agentic_settings(),
            })
            continue

        if m_type == "GET_GRAPH":
            handle_get_graph(host_ctx, msg)
            continue

        if m_type == "GET_HISTORY":
            history_key = _resolve_chat_key(msg)
            tab_id = msg.get("tabId")
            session_id = str(msg.get("sessionId") or "default")
            history = memory_store.get_history(history_key, session_id=session_id) if history_key else []
            sessions = memory_store.list_sessions(history_key) if history_key else []
            resolved_session_id = session_id
            if not history and sessions:
                for row in sessions:
                    sid = str(row.get("sessionId") or "").strip()
                    if not sid or sid.startswith("cell-debug-"):
                        continue
                    candidate = memory_store.get_history(history_key, session_id=sid)
                    if candidate:
                        history = candidate
                        resolved_session_id = sid
                        log(
                            f"[chat] Loaded latest session for {history_key}: "
                            f"{sid} ({len(history)} messages)"
                        )
                        break
            send_msg({
                "type": "HISTORY_DATA",
                "history": history,
                "sessions": sessions,
                "activeSessionId": resolved_session_id,
                "sessionId": resolved_session_id,
                "tabId": tab_id,
                "url": _normalized_url(msg.get("url") or ""),
                "notebookKey": history_key,
            })
            continue

        if m_type == "CLEAR_HISTORY":
            history_key = _resolve_chat_key(msg)
            tab_id = msg.get("tabId")
            session_id = msg.get("sessionId")
            if history_key:
                memory_store.clear_history(history_key, session_id=session_id)
            send_msg({
                "type": "HISTORY_CLEARED",
                "url": _normalized_url(msg.get("url") or ""),
                "notebookKey": history_key,
                "tabId": tab_id,
                "sessionId": session_id,
            })
            continue

        if m_type == "INSERT_CODE_CELL":
            url = _normalized_url(msg.get("url") or "")
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
    os.environ["NOTEBOOK_COPILOT_NATIVE_HOST"] = "1"
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
