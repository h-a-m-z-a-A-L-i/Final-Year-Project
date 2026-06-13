import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

try:
    from .config import *
    from .config import _HASHES_LOCK, _EXECUTION_STATE_LOCK
except Exception:
    try:
        from config import *
        from config import _HASHES_LOCK, _EXECUTION_STATE_LOCK
    except Exception:
        from testing.host.config import *
        from testing.host.config import _HASHES_LOCK, _EXECUTION_STATE_LOCK

try:
    from .dependency import _build_fallback_graph
except Exception:
    try:
        from dependency import _build_fallback_graph
    except Exception:
        from testing.host.dependency import _build_fallback_graph

try:
    from .persistence_helpers import _atomic_write_json, _load_execution_state, _load_hashes, _save_execution_state, _save_hashes, get_safe_filename, save_live_json, save_persistent_json, read_json_file
except Exception:
    try:
        from persistence_helpers import _atomic_write_json, _load_execution_state, _load_hashes, _save_execution_state, _save_hashes, get_safe_filename, save_live_json, save_persistent_json, read_json_file
    except Exception:
        from testing.host.persistence_helpers import _atomic_write_json, _load_execution_state, _load_hashes, _save_execution_state, _save_hashes, get_safe_filename, save_live_json, save_persistent_json, read_json_file


def _load_json_file(path):
    data = read_json_file(path)
    return data if isinstance(data, dict) else None


def _snapshot_has_cells(data: dict | None) -> bool:
    return bool(isinstance(data, dict) and isinstance(data.get("cells"), list) and data.get("cells"))


def _promote_live_snapshot_if_needed(url: str):
    filename = get_safe_filename(url)
    persistent_path = SCRAPED_DIR / "persistent" / filename
    if _snapshot_has_cells(_load_json_file(persistent_path)):
        return persistent_path

    live_path = SCRAPED_DIR / "live" / filename
    live_data = _load_json_file(live_path)
    if _snapshot_has_cells(live_data):
        save_persistent_json(live_data, url)
    return persistent_path


def _normalized_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), (parsed.path or "").rstrip("/"), "", "", ""))
    except Exception:
        return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")


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


def _push_graph(ctx: dict, url: str, tab_id):
    # Allow building the graph even when tab_id is missing. The caller
    # (extension background) may resolve the tab and forward GRAPH_DATA
    # back to the correct tab. We still include tabId in the outgoing
    # message when available (can be None).

    try:
        payload = build_graph_payload(ctx, url)
        payload["tabId"] = tab_id
        send_msg = ctx["send_msg"]
        send_msg(payload)
    except Exception as e:
        log = ctx["log"]
        log(f"Push Graph Error: {e}")
        send_msg = ctx["send_msg"]
        send_msg({"type": "GRAPH_DATA", "graph": [], "tabId": tab_id, "error": f"Graph generation failed: {e}", "url": url})


def build_graph_payload(ctx: dict, url: str) -> dict:
    dep_manager = ctx["dep_manager"]

    _promote_live_snapshot_if_needed(url)
    builder = dep_manager.get_builder(url)
    if builder is not None and hasattr(builder, "tracker") and hasattr(builder, "cells"):
        tracker = builder.tracker
        graph = []
        for num, data in builder.cells.items():
            graph.append({
                "cell_number": num,
                "input_preview": data.get("code", "")[:120],
                "dependencies": tracker.get_dependencies(num, transitive=False),
                "reverse_dependencies": tracker.get_reverse_dependencies(num),
            })
        return {"type": "GRAPH_DATA", "graph": graph, "error": None, "url": url}

    try:
        from .notebook_context import build_graph_list
    except Exception:
        from notebook_context import build_graph_list

    graph = build_graph_list(url)
    if graph:
        return {"type": "GRAPH_DATA", "graph": graph, "error": None, "url": url}

    fallback_graph = _build_fallback_graph(url)
    if fallback_graph is not None:
        return {"type": "GRAPH_DATA", "graph": fallback_graph, "error": None, "url": url}

    return {"type": "GRAPH_DATA", "graph": [], "error": "No notebook data available yet for this page.", "url": url}


def handle_get_graph(ctx: dict, msg: dict):
    _push_graph(ctx, _normalized_url(msg.get("url") or ""), msg.get("tabId"))


def handle_notebook_data(ctx: dict, msg: dict):
    dep_manager = ctx["dep_manager"]
    send_msg = ctx["send_msg"]
    log = ctx["log"]
    bot_state = ctx["bot_state"]
    bot_state_lock = ctx["bot_state_lock"]

    tab_url = _normalized_url(msg.get("tabUrl") or "unknown")
    tab_id = msg.get("tabId")
    notebook_id = msg.get("notebookId")
    try:
        try:
            from .notebook_identity import resolve_notebook_identity
            from .memory import memory_store
        except Exception:
            from notebook_identity import resolve_notebook_identity
            from memory import memory_store
        if tab_url and tab_url != "unknown":
            identity = resolve_notebook_identity(
                tab_url,
                notebook_id,
                memory_store=memory_store,
                log=log,
            )
            notebook_id = identity.get("notebookId") or notebook_id
            if str(identity.get("notebookKey") or "").startswith("kaggle:kernel:"):
                log(f"[notebook_identity] {tab_url} -> {identity.get('notebookKey')}")
    except Exception as e:
        log(f"[notebook_identity] Registration skipped: {e}")

    kernel_status = msg.get("kernelStatus")
    kernel_scenario = msg.get("kernelScenario", "unknown")
    kernel_state = msg.get("kernelState", {})
    if isinstance(tab_id, int):
        with bot_state_lock:
            bot_state["tabId"] = tab_id
            bot_state["url"] = tab_url
            bot_state["kernelScenario"] = kernel_scenario
            bot_state["kernelStatus"] = kernel_status
            bot_state["kernelState"] = kernel_state if isinstance(kernel_state, dict) else {}
    kernel_active = _kernel_is_active(kernel_status)
    kernel_scenario_norm = _normalize_kernel_scenario(kernel_scenario)

    log(f"[TAB {tab_id}] Kernel Scenario: {kernel_scenario} | Status: {kernel_status}")
    if isinstance(kernel_state, dict):
        log(f"[TAB {tab_id}]   Editor Loading: {kernel_state.get('editorLoading')}, Off: {kernel_state.get('off')}, HDD: {kernel_state.get('hdd')}")

    raw_cells = msg.get("cells", [])
    if not isinstance(raw_cells, list):
        raw_cells = []

    try:
        from .cell_index import normalize_notebook_cells
    except Exception:
        from cell_index import normalize_notebook_cells

    code_cells = []
    all_cells = []
    live_cells = []
    for i, cell in enumerate(raw_cells):
        cell_type = cell.get("type", "code")
        try:
            if cell.get("index") is not None:
                cell_index = int(cell.get("index"))
                if cell_index < 1:
                    cell_index = i + 1
            else:
                cell_index = i + 1
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
            
            live_cell = dict(cell)
            live_cell["type"] = "code"
            live_cell["index"] = cell_index
            live_cells.append(live_cell)
        elif cell_type == "markdown":
            markdown_cell = {
                "type": "markdown",
                "index": cell_index,
                "input": str(cell.get("input") or ""),
                "state": str(cell.get("state") or "open"),
            }
            all_cells.append((cell_index, cell_type, markdown_cell))
            
            live_cell = dict(cell)
            live_cell["type"] = "markdown"
            live_cell["index"] = cell_index
            live_cells.append(live_cell)

    now_iso = datetime.now(timezone.utc).isoformat()
    normalize_notebook_cells(live_cells)
    live_cells.sort(key=lambda cell: int(cell.get("index", 0)))
    live_data = {
        "tabUrl": tab_url,
        "title": str(msg.get("title", "notebook")),
        "lastUpdated": now_iso,
        "cells": live_cells,
    }
    
    save_live_json(live_data, tab_url)

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

    is_empty_notebook = (data_hash == "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945" or not code_cells)

    if not is_empty_notebook:
        should_save = False
        save_cells = []

        with _HASHES_LOCK:
            stored_hashes = _load_hashes()
            if stored_hashes.get(tab_url) != data_hash:
                should_save = True

        persistent_path = SCRAPED_DIR / "persistent" / get_safe_filename(tab_url)
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

            if _scenario_is_fresh(kernel_scenario_norm) and scenario_entered:
                notebook_state["kernel_session_started_at"] = now_iso
                notebook_state.pop("kernel_session_stopped_at", None)
                log(f"[Session] Kernel session STARTED at {now_iso}")
                try:
                    ppath = SCRAPED_DIR / "persistent" / get_safe_filename(tab_url)
                    if ppath.exists():
                        pdata = read_json_file(ppath) or {}
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
                    if _scenario_is_fresh(kernel_scenario_norm) and not seen_running:
                        saved_order = None
                        saved_title = ""
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

            for cell_index, cell_type, cell_data in all_cells:
                if cell_type == "markdown":
                    save_cells.append(cell_data)

            revision_state["cells"] = updated_cells
            revision_state["last_seen_at"] = now_iso
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

        existing_by_index = {}
        if persistent_path.is_file():
            try:
                existing_data = read_json_file(persistent_path)
                existing_cells = existing_data.get("cells", []) if isinstance(existing_data, dict) else []
                existing_by_index = {str(cell.get("index")): cell for cell in existing_cells if isinstance(cell, dict) and cell.get("index") is not None}
                for cell in save_cells:
                    prev_cell = existing_by_index.get(str(cell["index"]), {})
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
            if existing_by_index:
                for cell in save_cells:
                    if cell.get("type") == "markdown":
                        continue
                    prev_cell = existing_by_index.get(str(cell["index"]), {})
                    if not isinstance(prev_cell, dict):
                        continue
                    prev_order = prev_cell.get("execution_order")
                    prev_title = str(prev_cell.get("execution_title") or "")
                    inc_order = cell.get("execution_order")
                    if prev_title == "Cell is not executed yet":
                        prev_title = ""
                    if prev_order is None:
                        pass
                    else:
                        if inc_order is None:
                            cell["execution_order"] = prev_order
                            cell["execution_title"] = prev_title
                        else:
                            if _scenario_is_fresh(kernel_scenario_norm):
                                pass
                            elif _scenario_is_off(kernel_scenario_norm):
                                cell["execution_order"] = prev_order
                                cell["execution_title"] = prev_title
                            elif _scenario_is_reload(kernel_scenario_norm):
                                try:
                                    if int(inc_order) > int(prev_order):
                                        pass
                                    else:
                                        cell["execution_order"] = prev_order
                                        cell["execution_title"] = prev_title
                                except Exception:
                                    cell["execution_order"] = prev_order
                                    cell["execution_title"] = prev_title

            for c in save_cells:
                if c.get("type") == "markdown":
                    continue
                if "execution_timestamp" in c:
                    try:
                        del c["execution_timestamp"]
                    except Exception:
                        pass
                order = c.get("execution_order")
                title = str(c.get("execution_title") or "").strip()
                if order is not None and not title:
                    try:
                        c["execution_title"] = f"Execution #{int(order)}"
                    except Exception:
                        c["execution_title"] = "Execution"

            save_cells.sort(key=lambda cell: int(cell.get("index", 0)))

            final_data = {
                "tabUrl": tab_url,
                "title": str(msg.get("title", "notebook")),
                "lastUpdated": now_iso,
                "cells": save_cells,
            }

            rev_state = revisions.get(data_hash, {})
            seen_count = int(rev_state.get("seen_count") or 0)
            should_write_persistent = False
            if not persistent_path.exists() or seen_count >= 1:
                should_write_persistent = True

            if not should_write_persistent and persistent_path.exists():
                try:
                    existing = read_json_file(persistent_path)
                    existing_by_idx = {int(c.get("index", 0)): c for c in existing.get("cells", []) if isinstance(c, dict)}
                    incoming_cells = final_data.get("cells", [])
                    if incoming_cells and len(incoming_cells) > 0:
                        if len(existing_by_idx) != len(incoming_cells):
                            should_write_persistent = True
                        else:
                            for c in incoming_cells:
                                idx = int(c.get("index", 0))
                                prev = existing_by_idx.get(idx, {})
                                if (
                                    prev.get("type") != c.get("type")
                                    or prev.get("input") != c.get("input")
                                    or prev.get("output") != c.get("output")
                                    or str(prev.get("execution_order")) != str(c.get("execution_order"))
                                ):
                                    should_write_persistent = True
                                    break
                except Exception:
                    pass

            if should_write_persistent:
                save_persistent_json(final_data, tab_url)
                with _HASHES_LOCK:
                    stored_hashes = _load_hashes()
                    stored_hashes[tab_url] = data_hash
                    _save_hashes(stored_hashes)

        _push_graph(ctx, tab_url, tab_id)
    else:
        _push_graph(ctx, tab_url, tab_id)

    send_msg({"ok": True})
