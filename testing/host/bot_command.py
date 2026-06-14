"""Unified notebook bot command execution (browser + optional persistence sync)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

def _send_msg(obj: dict) -> None:
    try:
        from .dispatcher import send_msg
    except Exception:
        from dispatcher import send_msg
    send_msg(obj)

_PENDING_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}

# Browser commands dispatch to the extension without waiting unless waitForResult is set.
def _is_fire_and_forget(cmd: dict) -> bool:
    if cmd.get("waitForResult") is True or cmd.get("wait_for_result") is True:
        return False
    return True


def _build_dispatched_result(cmd: dict, mapped: dict) -> dict:
    dom_index = mapped.get("cellIndex")
    if dom_index is None:
        dom_index = cmd.get("dom_index")
    app_index = None
    if isinstance(dom_index, int):
        try:
            from .cell_index import dom_to_app
        except Exception:
            from cell_index import dom_to_app
        app_index = dom_to_app(dom_index)
    elif cmd.get("app_index") is not None:
        app_index = cmd.get("app_index")
    return {
        "ok": True,
        "dispatched": True,
        "phase": "dispatched",
        "domIndex": dom_index,
        "appIndex": app_index,
        "cellIndex": app_index,
    }


def _pick_url(cmd: dict) -> str:
    return str(cmd.get("url") or cmd.get("tabUrl") or cmd.get("tab_url") or "").strip()


def _pick_tab_id(cmd: dict) -> int | None:
    tab_id = cmd.get("tabId") if isinstance(cmd.get("tabId"), int) else None
    if tab_id is not None and tab_id <= 0:
        return None
    return tab_id


def _pick_raw_cell_index(cmd: dict) -> Any:
    for key in ("dom_index", "domIndex", "cellIndex", "cell_index", "index"):
        if key in cmd and cmd.get(key) is not None:
            return cmd.get(key)
    return None


def _dom_index_from_cmd(cmd: dict, *, default_basis: str = "dom") -> int | None:
    """
    Resolve 0-based DOM index for extension messages (data-windowed-list-index).

    - Browser tools pass dom_index / index_basis=dom (default for click).
    - Legacy insert/UI flows use 1-based app indices (default_basis=app).
    """
    try:
        from .cell_index import app_to_dom
    except Exception:
        from cell_index import app_to_dom

    for key in ("dom_index", "domIndex"):
        raw = cmd.get(key)
        if isinstance(raw, int) and raw >= 0:
            return raw

    raw = _pick_raw_cell_index(cmd)
    if raw is None:
        return None

    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None

    basis = str(cmd.get("index_basis") or cmd.get("indexBasis") or default_basis).strip().lower()
    if basis in {"app", "1", "1-based", "one_based"}:
        if val < 1:
            return None
        return app_to_dom(val)

    if val >= 0:
        return val
    return None


def _normalize_action(cmd: dict) -> str:
    action = str(cmd.get("action") or cmd.get("type") or "").strip().lower()
    if action not in {"click_selector", "click-selector", "clickselector"} and cmd.get("selector"):
        return "click_selector"
    aliases = {
        "delete_cell": "delete_by_index",
        "deletecell": "delete_by_index",
        "delete_cell_by_index": "delete_by_index",
    }
    return aliases.get(action, action)


def _inner_ok(result: dict | None) -> bool:
    if not isinstance(result, dict):
        return False
    inner = result.get("result")
    if isinstance(inner, dict) and inner.get("ok") is False:
        return False
    if result.get("ok") is False:
        return False
    if isinstance(inner, dict):
        return bool(inner.get("ok", True))
    return bool(result.get("ok", True))


def build_result_event(cmd: dict, ok: bool, result: dict, error: str | None = None) -> dict:
    action = _normalize_action(cmd)
    request_id = cmd.get("requestId")
    url = _pick_url(cmd)
    tab_id = _pick_tab_id(cmd)
    if isinstance(result.get("tabId"), int) and result.get("tabId") > 0:
        tab_id = result.get("tabId")
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": ok,
        "type": (action or "unknown").upper(),
        "url": url,
        "requestId": request_id,
        "result": result,
    }
    if isinstance(tab_id, int):
        payload["tabId"] = tab_id
    if error:
        payload["error"] = error
    return payload


def extension_message_to_event(msg: dict) -> dict:
    msg_type = str(msg.get("type") or "")
    inner = msg.get("result") if isinstance(msg.get("result"), dict) else {}
    ok = msg_type.endswith("_RESULT") and _inner_ok({"ok": True, "result": inner})
    if msg_type.endswith("_ERROR"):
        ok = False
    cmd_stub = {
        "requestId": msg.get("requestId"),
        "url": msg.get("url") or msg.get("tabUrl"),
        "tabId": msg.get("tabId"),
        "action": msg.get("tunnel") or msg_type.replace("_RESULT", "").replace("_ERROR", ""),
    }
    error = None if ok else str(inner.get("error") or msg.get("error") or "extension command failed")
    event = build_result_event(cmd_stub, ok, inner if isinstance(inner, dict) else {}, error)
    event["type"] = msg_type.replace("_RESULT", "").replace("_ERROR", "") or event.get("type")
    if msg.get("tunnel"):
        event["tunnel"] = msg.get("tunnel")
    if msg.get("diagnostics") is not None:
        event["diagnostics"] = msg.get("diagnostics")
    return event


def complete_bot_result(msg: dict) -> dict:
    event = extension_message_to_event(msg)
    request_id = msg.get("requestId")
    if request_id:
        with _PENDING_LOCK:
            slot = _PENDING.get(request_id)
            if slot is not None:
                slot["event"] = event
                slot["waiter"].set()
    return event


def map_command_to_native(cmd: dict) -> dict | None:
    action = _normalize_action(cmd)
    url = _pick_url(cmd)
    tab_id = _pick_tab_id(cmd)
    request_id = cmd.get("requestId") or str(uuid.uuid4())

    mapped: dict[str, Any] = {"requestId": request_id}
    if url:
        mapped["url"] = url
    if isinstance(tab_id, int):
        mapped["tabId"] = tab_id

    if action in {"click_selector", "click-selector", "clickselector"} or cmd.get("selector"):
        mapped["type"] = "CLICK_SELECTOR"
        mapped["tunnel"] = "click_selector"
        mapped["selector"] = cmd.get("selector") or cmd.get("sel")
        return mapped

    if action in {"click", "click_cell", "click_cell_by_index", "clickcell"}:
        mapped["type"] = "CLICK_CELL_BY_INDEX"
        mapped["tunnel"] = "click_cell"
        dom_index = _dom_index_from_cmd(cmd, default_basis=cmd.get("index_basis") or "app")
        if dom_index is None:
            return None
        mapped["cellIndex"] = dom_index
        mapped["dom_index"] = dom_index
        mapped["scrollIntoView"] = cmd.get("scrollIntoView", True)
        mapped["runCell"] = False
        wait_ms = cmd.get("maxWaitMs") if cmd.get("maxWaitMs") is not None else cmd.get("max_wait_ms")
        if wait_ms is None:
            mapped["maxWaitMs"] = 400
        else:
            try:
                mapped["maxWaitMs"] = int(wait_ms)
            except Exception:
                mapped["maxWaitMs"] = 400
        return mapped

    if action in {"select_cell_by_index", "selectcellbyindex"}:
        mapped["type"] = "SELECT_CELL_BY_INDEX"
        mapped["tunnel"] = "select_cell_by_index"
        dom_index = _dom_index_from_cmd(cmd, default_basis=cmd.get("index_basis") or "app")
        if dom_index is None:
            return None
        mapped["cellIndex"] = dom_index
        mapped["dom_index"] = dom_index
        mapped["scrollIntoView"] = cmd.get("scrollIntoView", True)
        mapped["runCell"] = False
        wait_ms = cmd.get("maxWaitMs") if cmd.get("maxWaitMs") is not None else cmd.get("max_wait_ms")
        if wait_ms is None:
            mapped["maxWaitMs"] = 400
        else:
            try:
                mapped["maxWaitMs"] = int(wait_ms)
            except Exception:
                mapped["maxWaitMs"] = 400
        return mapped

    if action in {"run_cell", "run_cell_by_index"}:
        mapped["type"] = "RUN_CELL_BY_INDEX"
        mapped["tunnel"] = "run_cell"
        dom_index = _dom_index_from_cmd(cmd, default_basis=cmd.get("index_basis") or "app")
        if dom_index is None:
            return None
        mapped["cellIndex"] = dom_index
        mapped["dom_index"] = dom_index
        mapped["scrollIntoView"] = cmd.get("scrollIntoView", True)
        wait_ms = cmd.get("maxWaitMs") if cmd.get("maxWaitMs") is not None else cmd.get("max_wait_ms")
        if wait_ms is None:
            mapped["maxWaitMs"] = 240
        else:
            try:
                mapped["maxWaitMs"] = int(wait_ms)
            except Exception:
                mapped["maxWaitMs"] = 240
        return mapped

    if action in {"creating_markdown_by_index", "creating_markdown"}:
        mapped["type"] = "CREATING_MARKDOWN_BY_INDEX"
        mapped["tunnel"] = "creating_markdown_by_index"
        dom_index = _dom_index_from_cmd(cmd, default_basis=cmd.get("index_basis") or "app")
        if dom_index is None:
            return None
        mapped["cellIndex"] = dom_index
        mapped["dom_index"] = dom_index
        return mapped

    if action == "insert_cell":
        mapped["type"] = "INSERT_CELL"
        mapped["tunnel"] = "insert_cell"
        mapped["direction"] = cmd.get("direction", "below")
        dom_index = _dom_index_from_cmd(cmd, default_basis=cmd.get("index_basis") or "app")
        if dom_index is not None:
            mapped["cellIndex"] = dom_index
            mapped["dom_index"] = dom_index
        wait_ms = cmd.get("maxWaitMs") if cmd.get("maxWaitMs") is not None else cmd.get("max_wait_ms")
        if wait_ms is not None:
            try:
                mapped["maxWaitMs"] = int(wait_ms)
            except Exception:
                pass
        return mapped

    if action in {"send_key", "sendkey"}:
        mapped["type"] = "SEND_KEY"
        mapped["tunnel"] = "send_key"
        mapped["key"] = cmd.get("key")
        return mapped

    if action in {"send_keys", "sendkeys"}:
        mapped["type"] = "SEND_KEYS"
        mapped["tunnel"] = "send_keys"
        mapped["keys"] = cmd.get("keys")
        return mapped

    if action in {"set_cell_content", "set_cell_content_by_index"}:
        mapped["type"] = "SET_CELL_CONTENT"
        mapped["tunnel"] = "set_cell_content"
        dom_index = _dom_index_from_cmd(cmd, default_basis=cmd.get("index_basis") or "app")
        if dom_index is None:
            return None
        mapped["cellIndex"] = dom_index
        mapped["dom_index"] = dom_index
        mapped["content"] = cmd.get("content") or cmd.get("input") or ""
        wait_ms = cmd.get("maxWaitMs") if cmd.get("maxWaitMs") is not None else cmd.get("max_wait_ms")
        if wait_ms is None:
            mapped["maxWaitMs"] = 160
        else:
            try:
                mapped["maxWaitMs"] = int(wait_ms)
            except Exception:
                mapped["maxWaitMs"] = 160
        return mapped

    if action == "delete_by_index":
        mapped["type"] = "DELETE_CELL"
        mapped["tunnel"] = "delete_by_index"
        dom_index = _dom_index_from_cmd(cmd, default_basis=cmd.get("index_basis") or "app")
        if dom_index is None:
            return None
        mapped["cellIndex"] = dom_index
        mapped["dom_index"] = dom_index
        mapped["scrollIntoView"] = cmd.get("scrollIntoView", True)
        wait_ms = cmd.get("maxWaitMs") if cmd.get("maxWaitMs") is not None else cmd.get("max_wait_ms")
        if wait_ms is None:
            mapped["maxWaitMs"] = 600
        else:
            try:
                mapped["maxWaitMs"] = int(wait_ms)
            except Exception:
                mapped["maxWaitMs"] = 600
        return mapped

    return None


def should_use_browser(cmd: dict) -> bool:
    url = _pick_url(cmd)
    tab_id = _pick_tab_id(cmd)
    action = _normalize_action(cmd)
    if action in {"notebook_graph_query", "sync_persistence"}:
        return False
    return bool(url) or isinstance(tab_id, int)


def _sync_persistence(action: str, cmd: dict, browser_result: dict) -> None:
    try:
        from .tool_registry import sync_persistence_for_action
    except Exception:
        try:
            from tool_registry import sync_persistence_for_action
        except Exception:
            return
    try:
        sync_persistence_for_action(action, cmd, browser_result)
    except Exception:
        pass


def _execute_browser_command(cmd: dict, timeout: float = 12.0) -> dict:
    """Send one mapped browser command to the extension and wait (no composite flow re-entry)."""
    action = _normalize_action(cmd)
    request_id = str(cmd.get("requestId") or uuid.uuid4())
    cmd = dict(cmd)
    cmd["requestId"] = request_id

    if not should_use_browser(cmd):
        return build_result_event(
            cmd,
            False,
            {"ok": False, "error": "url is required (open the notebook tab and pass its URL)"},
            "url is required",
        )

    mapped = map_command_to_native(cmd)
    if mapped is None:
        return build_result_event(
            cmd,
            False,
            {"ok": False, "error": f"Unsupported action: {action or 'missing'}"},
            f"Unsupported action: {action or 'missing'}",
        )

    if mapped.get("cellIndex") is None and action in {"click", "click_cell", "click_cell_by_index", "select_cell_by_index"}:
        return build_result_event(
            cmd,
            False,
            {"ok": False, "error": "cell_index is required (0-based DOM index, data-windowed-list-index)"},
            "cell_index is required",
        )

    waiter = threading.Event()
    with _PENDING_LOCK:
        _PENDING[request_id] = {"event": None, "waiter": waiter}

    try:
        _send_msg(mapped)
    except Exception as exc:
        with _PENDING_LOCK:
            _PENDING.pop(request_id, None)
        return build_result_event(cmd, False, {"ok": False, "error": str(exc)}, str(exc))

    if _is_fire_and_forget(cmd):
        with _PENDING_LOCK:
            _PENDING.pop(request_id, None)
        return build_result_event(cmd, True, _build_dispatched_result(cmd, mapped))

    if not waiter.wait(max(0.5, float(timeout))):
        with _PENDING_LOCK:
            _PENDING.pop(request_id, None)
        return build_result_event(
            cmd,
            False,
            {"ok": False, "error": "timeout waiting for extension (is the notebook tab open?)"},
            "timeout",
        )

    with _PENDING_LOCK:
        slot = _PENDING.pop(request_id, None)
    event = (slot or {}).get("event")
    if not isinstance(event, dict):
        return build_result_event(
            cmd,
            False,
            {"ok": False, "error": "no extension response"},
            "no extension response",
        )

    if event.get("ok"):
        _sync_persistence(action, cmd, event.get("result") or {})
    return event


def execute_bot_command_sync(cmd: dict, timeout: float = 12.0) -> dict:
    action = _normalize_action(cmd)
    cmd = dict(cmd)
    cmd["requestId"] = str(cmd.get("requestId") or uuid.uuid4())

    if action in {"edit_cell_by_index", "edit_cell"}:
        return run_edit_cell_flow(cmd, timeout=timeout)

    if action in {"set_cell_content", "set_cell_content_by_index"}:
        return run_set_cell_content_flow(cmd, timeout=timeout)

    if action == "delete_by_index":
        return run_delete_cell_flow(cmd, timeout=timeout)

    return _execute_browser_command(cmd, timeout=timeout)


def _result_ok(event: dict | None) -> bool:
    return bool(event and event.get("ok") and _inner_ok(event))


def _finalize_flow_result(outer_cmd: dict, step_event: dict | None, extra: dict | None = None) -> dict:
    """Re-parent a nested browser step result onto the outer tool requestId."""
    outer_id = str(outer_cmd.get("requestId") or "")
    if (
        isinstance(step_event, dict)
        and outer_id
        and str(step_event.get("requestId") or "") == outer_id
    ):
        return step_event

    ok = _result_ok(step_event)
    inner: dict[str, Any] = {}
    if isinstance(step_event, dict):
        candidate = step_event.get("result")
        if isinstance(candidate, dict):
            inner = dict(candidate)
    if isinstance(extra, dict):
        inner = {**inner, **extra}

    error = None
    if not ok:
        if isinstance(step_event, dict):
            error = str(step_event.get("error") or inner.get("error") or "step failed")
        else:
            error = "step failed"
    return build_result_event(outer_cmd, ok, inner, error)


def run_insert_cell_flow(cmd: dict, timeout: float = 12.0) -> dict:
    dom_idx = _dom_index_from_cmd(cmd, default_basis="app")
    direction = cmd.get("direction", "below")
    url = _pick_url(cmd)
    click_timeout = float(cmd.get("click_timeout") or min(timeout, 4.0))
    insert_timeout = float(cmd.get("insert_timeout") or min(timeout, 4.0))

    if dom_idx is not None:
        click_cmd = dict(cmd)
        click_cmd["action"] = "click"
        click_cmd["index_basis"] = "dom"
        click_cmd["cellIndex"] = dom_idx
        click_cmd["dom_index"] = dom_idx
        click_cmd["url"] = url
        click_cmd["requestId"] = str(uuid.uuid4())
        click_cmd["maxWaitMs"] = cmd.get("maxWaitMs", 160)
        click_event = _execute_browser_command(click_cmd, timeout=click_timeout)
        if not _result_ok(click_event):
            return _finalize_flow_result(cmd, click_event, {"phase": "click_failed"})
        tab_id = _pick_tab_id(cmd) or (click_event.get("result") or {}).get("tabId") or click_event.get("tabId")
    else:
        tab_id = _pick_tab_id(cmd)

    insert_cmd = {
        "action": "insert_cell",
        "requestId": str(uuid.uuid4()),
        "direction": direction,
        "url": url,
    }
    if dom_idx is not None:
        insert_cmd["cellIndex"] = dom_idx
    if isinstance(tab_id, int):
        insert_cmd["tabId"] = tab_id

    # Direct mapped insert (avoid re-entering insert_cell composite)
    mapped = map_command_to_native(insert_cmd)
    if mapped is None:
        return build_result_event(cmd, False, {"ok": False, "error": "insert mapping failed"}, "insert mapping failed")

    request_id = insert_cmd["requestId"]
    waiter = threading.Event()
    with _PENDING_LOCK:
        _PENDING[request_id] = {"event": None, "waiter": waiter}
    try:
        _send_msg(mapped)
    except Exception as exc:
        with _PENDING_LOCK:
            _PENDING.pop(request_id, None)
        return build_result_event(cmd, False, {"ok": False, "error": str(exc)}, str(exc))

    if not waiter.wait(max(0.5, insert_timeout)):
        with _PENDING_LOCK:
            _PENDING.pop(request_id, None)
        return build_result_event(cmd, False, {"ok": False, "error": "timeout"}, "timeout")

    with _PENDING_LOCK:
        slot = _PENDING.pop(request_id, None)
    event = (slot or {}).get("event")
    inner = (event.get("result") or {}) if isinstance(event, dict) else {}
    flow_extra = None
    if isinstance(event, dict) and event.get("ok"):
        try:
            from .cell_index import dom_to_app
        except Exception:
            from cell_index import dom_to_app
        app_anchor = dom_to_app(dom_idx) if dom_idx is not None else None
        _sync_persistence(
            "insert_cell",
            {**cmd, "index": app_anchor, "direction": direction},
            inner,
        )
        if dom_idx is not None:
            try:
                inner = dict(inner)
                if direction == "above":
                    new_dom = int(dom_idx)
                else:
                    new_dom = int(dom_idx) + 1
                inner.setdefault("cellIndex", new_dom)
                inner.setdefault("domIndex", new_dom)
                inner.setdefault("insertedBelow", int(dom_idx))
                flow_extra = inner
            except Exception:
                pass
    return _finalize_flow_result(cmd, event, flow_extra)


def run_set_cell_content_flow(cmd: dict, timeout: float = 12.0) -> dict:
    dom_idx = _dom_index_from_cmd(cmd, default_basis=cmd.get("index_basis") or "dom")
    content = str(cmd.get("content") or cmd.get("input") or "")
    if dom_idx is None:
        return build_result_event(
            cmd,
            False,
            {"ok": False, "error": "cell_index is required (0-based DOM index)"},
            "cell_index is required",
        )

    url = _pick_url(cmd)
    tab_id = _pick_tab_id(cmd)
    request_id = str(cmd.get("requestId") or uuid.uuid4())

    browser_cmd = {
        "action": "set_cell_content",
        "requestId": request_id,
        "cellIndex": dom_idx,
        "dom_index": dom_idx,
        "index_basis": "dom",
        "content": content,
        "url": url,
        "maxWaitMs": cmd.get("maxWaitMs", 600),
        "timeout": cmd.get("timeout", timeout),
    }
    if isinstance(tab_id, int):
        browser_cmd["tabId"] = tab_id

    wait_timeout = float(browser_cmd.get("timeout") or timeout)
    set_event = _execute_browser_command(browser_cmd, timeout=wait_timeout)
    if not _result_ok(set_event):
        inner = set_event.get("result") if isinstance(set_event.get("result"), dict) else {}
        error = str(set_event.get("error") or inner.get("error") or "set_cell_content failed")
        return build_result_event(
            cmd,
            False,
            {"ok": False, "error": error, **(inner if isinstance(inner, dict) else {})},
            error,
        )

    inner = set_event.get("result") if isinstance(set_event.get("result"), dict) else {}

    try:
        from .cell_index import dom_to_app
        from .tool_registry import sync_persistence_for_action
    except Exception:
        from cell_index import dom_to_app
        from tool_registry import sync_persistence_for_action

    try:
        sync_persistence_for_action(
            "edit_cell_by_index",
            {"url": url, "cell_index": dom_to_app(dom_idx), "content": content},
            {"ok": True},
        )
    except Exception:
        pass

    app_idx = dom_to_app(dom_idx)
    return build_result_event(
        cmd,
        True,
        {
            "ok": True,
            "phase": "content_set",
            "domIndex": dom_idx,
            "appIndex": app_idx,
            "cellIndex": app_idx,
            "chars": inner.get("chars") or len(content),
            "strategy": inner.get("strategy"),
            "dataWindowedListIndex": inner.get("dataWindowedListIndex") or str(dom_idx),
        },
    )


def run_insert_code_below_flow(cmd: dict, timeout: float = 12.0) -> dict:
    """Insert a new code cell below anchor, then paste content into it."""
    dom_anchor = _dom_index_from_cmd(cmd, default_basis="app")
    if dom_anchor is None:
        return build_result_event(cmd, False, {"ok": False, "error": "index is required"}, "index is required")

    direction = str(cmd.get("direction") or "below").strip().lower()
    click_timeout = float(cmd.get("click_timeout") or min(timeout, 4.0))
    insert_timeout = float(cmd.get("insert_timeout") or min(timeout, 4.0))
    edit_timeout = float(cmd.get("edit_timeout") or min(timeout, 5.0))

    insert_cmd = dict(cmd)
    insert_cmd["action"] = "insert_cell"
    insert_cmd["direction"] = direction
    insert_cmd["index_basis"] = "dom"
    insert_cmd["cellIndex"] = dom_anchor
    insert_cmd["dom_index"] = dom_anchor
    insert_cmd["click_timeout"] = click_timeout
    insert_cmd["insert_timeout"] = insert_timeout
    insert_cmd["maxWaitMs"] = cmd.get("maxWaitMs", 160)
    insert_event = run_insert_cell_flow(insert_cmd, timeout=click_timeout + insert_timeout)
    if not _result_ok(insert_event):
        return _finalize_flow_result(cmd, insert_event, {"phase": "insert_failed"})

    inner = insert_event.get("result") or {}
    new_dom = inner.get("domIndex")
    if new_dom is None:
        new_dom = inner.get("cellIndex")
    if new_dom is None:
        try:
            if direction == "above":
                new_dom = int(dom_anchor)
            else:
                new_dom = int(dom_anchor) + 1
        except Exception:
            return build_result_event(
                cmd,
                False,
                {"ok": False, "error": "Could not determine new cell index after insert"},
                "missing new cell index",
            )

    content = str(cmd.get("content") or cmd.get("input") or "")
    tab_id = _pick_tab_id(cmd) or (insert_event.get("result") or {}).get("tabId") or insert_event.get("tabId")
    edit_cmd = dict(cmd)
    edit_cmd["action"] = "set_cell_content"
    edit_cmd["index_basis"] = "dom"
    edit_cmd["cellIndex"] = new_dom
    edit_cmd["dom_index"] = new_dom
    edit_cmd["content"] = content
    edit_cmd["maxWaitMs"] = cmd.get("maxWaitMs", 160)
    edit_cmd["timeout"] = edit_timeout
    if isinstance(tab_id, int):
        edit_cmd["tabId"] = tab_id
    set_event = run_set_cell_content_flow(edit_cmd, timeout=edit_timeout)
    if not _result_ok(set_event):
        return build_result_event(
            cmd,
            False,
            {
                "ok": False,
                "phase": "insert_ok_edit_failed",
                "insertedBelow": dom_anchor,
                "newDomIndex": new_dom,
                "insert": insert_event,
                "edit": set_event,
            },
            "insert succeeded but setting cell content failed",
        )

    return build_result_event(
        cmd,
        True,
        {
            "ok": True,
            "phase": "insert_code_below_complete",
            "insertedBelow": int(dom_anchor),
            "anchorDomIndex": int(dom_anchor),
            "newDomIndex": int(new_dom),
            "new_cell_index": int(new_dom),
            "chars": len(content),
            "direction": direction,
        },
    )


def run_delete_cell_flow(cmd: dict, timeout: float = 12.0) -> dict:
    """Select cell (same as select_cell_by_index), then click the delete toolbar button."""
    delete_cmd = dict(cmd)
    delete_cmd.setdefault("maxWaitMs", 600)
    delete_timeout = float(cmd.get("delete_timeout") or timeout)
    delete_event = _execute_browser_command(delete_cmd, timeout=delete_timeout)
    return _finalize_flow_result(cmd, delete_event)


def run_creating_markdown_flow(cmd: dict, timeout: float = 12.0) -> dict:
    dom_idx = _dom_index_from_cmd(cmd, default_basis="app")
    if dom_idx is None:
        return build_result_event(cmd, False, {"ok": False, "error": "index is required"}, "index is required")

    url = _pick_url(cmd)
    tab_id = _pick_tab_id(cmd)

    click_cmd = {
        "action": "click",
        "requestId": str(uuid.uuid4()),
        "cellIndex": dom_idx,
        "dom_index": dom_idx,
        "index_basis": "dom",
        "url": url,
        "runCell": False,
    }
    if tab_id is not None:
        click_cmd["tabId"] = tab_id
    click_event = execute_bot_command_sync(click_cmd, timeout=timeout)
    if not _result_ok(click_event):
        return click_event

    tab_id = tab_id or (click_event.get("result") or {}).get("tabId") or click_event.get("tabId")

    insert_cmd = {
        "action": "insert_cell",
        "requestId": str(uuid.uuid4()),
        "direction": "above",
        "url": url,
        "cellIndex": dom_idx,
        "dom_index": dom_idx,
        "index_basis": "dom",
    }
    if isinstance(tab_id, int):
        insert_cmd["tabId"] = tab_id
    insert_event = execute_bot_command_sync(insert_cmd, timeout=timeout)
    if not _result_ok(insert_event):
        return insert_event

    time.sleep(0.5)
    focus_cmd = {
        "action": "click",
        "requestId": str(uuid.uuid4()),
        "cellIndex": dom_idx,
        "dom_index": dom_idx,
        "index_basis": "dom",
        "url": url,
    }
    if isinstance(tab_id, int):
        focus_cmd["tabId"] = tab_id
    focus_event = execute_bot_command_sync(focus_cmd, timeout=timeout)
    if not _result_ok(focus_event):
        return focus_event

    for key in ("Escape", "m"):
        time.sleep(0.3 if key == "m" else 0.2)
        key_cmd = {"action": "send_key", "requestId": str(uuid.uuid4()), "key": key, "url": url}
        if isinstance(tab_id, int):
            key_cmd["tabId"] = tab_id
        key_event = execute_bot_command_sync(key_cmd, timeout=timeout)
        if not _result_ok(key_event):
            return key_event

    return build_result_event(
        cmd,
        True,
        {"ok": True, "phase": "markdown_created", "domIndex": dom_idx, "cellIndex": dom_idx},
    )


def run_edit_and_run_flow(cmd: dict, timeout: float = 12.0) -> dict:
    """Replace cell source, then execute that cell in the kernel."""
    dom_idx = _dom_index_from_cmd(cmd, default_basis="app")
    content = str(cmd.get("content") or cmd.get("input") or "")
    if dom_idx is None:
        return build_result_event(cmd, False, {"ok": False, "error": "cell_index is required"}, "cell_index is required")

    edit_timeout = float(cmd.get("edit_timeout") or min(timeout, 5.0))
    run_timeout = float(cmd.get("run_timeout") or min(timeout, 6.0))

    edit_cmd = dict(cmd)
    edit_cmd["action"] = "set_cell_content"
    edit_cmd["index_basis"] = "dom"
    edit_cmd["cellIndex"] = dom_idx
    edit_cmd["dom_index"] = dom_idx
    edit_cmd["content"] = content
    edit_cmd["maxWaitMs"] = cmd.get("maxWaitMs", 160)
    edit_cmd["timeout"] = edit_timeout
    set_event = run_set_cell_content_flow(edit_cmd, timeout=edit_timeout)
    if not _result_ok(set_event):
        return _finalize_flow_result(cmd, set_event, {"phase": "edit_failed"})

    inner_edit = set_event.get("result") if isinstance(set_event.get("result"), dict) else {}
    tab_id = _pick_tab_id(cmd) or set_event.get("tabId") or inner_edit.get("tabId")

    run_cmd = dict(cmd)
    run_cmd["action"] = "run_cell"
    run_cmd["index_basis"] = "dom"
    run_cmd["cellIndex"] = dom_idx
    run_cmd["dom_index"] = dom_idx
    run_cmd["runCell"] = True
    run_cmd["maxWaitMs"] = cmd.get("run_maxWaitMs", cmd.get("maxWaitMs", 240))
    run_cmd["timeout"] = run_timeout
    if isinstance(tab_id, int):
        run_cmd["tabId"] = tab_id
    run_event = _execute_browser_command(run_cmd, timeout=run_timeout)
    if not _result_ok(run_event):
        inner_run = run_event.get("result") if isinstance(run_event.get("result"), dict) else {}
        try:
            from .cell_index import dom_to_app
        except Exception:
            from cell_index import dom_to_app
        return build_result_event(
            cmd,
            False,
            {
                "ok": False,
                "phase": "edit_ok_run_failed",
                "domIndex": dom_idx,
                "appIndex": dom_to_app(dom_idx),
                "edit": inner_edit,
                "run": inner_run,
            },
            str(run_event.get("error") or inner_run.get("error") or "run_cell failed after edit"),
        )

    inner_run = run_event.get("result") if isinstance(run_event.get("result"), dict) else {}
    try:
        from .cell_index import dom_to_app
    except Exception:
        from cell_index import dom_to_app
    app_idx = dom_to_app(dom_idx)
    try:
        from .tool_registry import sync_persistence_for_action
    except Exception:
        try:
            from tool_registry import sync_persistence_for_action
        except Exception:
            sync_persistence_for_action = None
    if sync_persistence_for_action:
        try:
            sync_persistence_for_action(
                "edit_cell_by_index",
                {"url": _pick_url(cmd), "cell_index": app_idx, "content": content},
                {"ok": True},
            )
        except Exception:
            pass

    return build_result_event(
        cmd,
        True,
        {
            "ok": True,
            "phase": "edit_and_run_complete",
            "domIndex": dom_idx,
            "appIndex": app_idx,
            "cellIndex": app_idx,
            "chars": inner_edit.get("chars") or len(content),
            "edit_strategy": inner_edit.get("strategy"),
            "run_strategy": inner_run.get("strategy"),
            "command_id": inner_run.get("commandId"),
        },
    )


def run_edit_cell_flow(cmd: dict, timeout: float = 12.0) -> dict:
    dom_idx = _dom_index_from_cmd(cmd, default_basis="app")
    content = cmd.get("content") or cmd.get("input") or ""
    if dom_idx is None:
        return build_result_event(cmd, False, {"ok": False, "error": "cell_index is required"}, "cell_index is required")

    set_cmd = dict(cmd)
    set_cmd["action"] = "set_cell_content"
    set_cmd["index_basis"] = "dom"
    set_cmd["cellIndex"] = dom_idx
    set_cmd["dom_index"] = dom_idx
    set_cmd["content"] = content
    if not str(content or "").strip():
        set_cmd["content"] = ""

    try:
        from .bot_tool_utils import is_retriable_browser_error
    except Exception:
        from bot_tool_utils import is_retriable_browser_error

    last_event = None
    for attempt in range(2):
        last_event = run_set_cell_content_flow(set_cmd, timeout=timeout)
        if _result_ok(last_event):
            return last_event
        err = ""
        if isinstance(last_event, dict):
            inner = last_event.get("result") if isinstance(last_event.get("result"), dict) else {}
            err = str(last_event.get("error") or inner.get("error") or "")
        if attempt == 0 and is_retriable_browser_error(err):
            time.sleep(0.6)
            continue
        break
    return last_event or build_result_event(cmd, False, {"ok": False, "error": "edit failed"}, "edit failed")


def execute_bot_command(cmd: dict, timeout: float = 12.0) -> dict:
    """Native host when inside host.py; JSONL queue for CLI smoke tests."""
    try:
        from .bot_command_client import execute_bot_command as _route
    except Exception:
        from bot_command_client import execute_bot_command as _route
    return _route(cmd, timeout=timeout)
