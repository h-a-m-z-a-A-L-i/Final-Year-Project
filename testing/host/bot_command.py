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


def _pick_url(cmd: dict) -> str:
    return str(cmd.get("url") or cmd.get("tabUrl") or cmd.get("tab_url") or "").strip()


def _pick_tab_id(cmd: dict) -> int | None:
    tab_id = cmd.get("tabId") if isinstance(cmd.get("tabId"), int) else None
    if tab_id is not None and tab_id <= 0:
        return None
    return tab_id


def _normalize_action(cmd: dict) -> str:
    action = str(cmd.get("action") or cmd.get("type") or "").strip().lower()
    if action not in {"click_selector", "click-selector", "clickselector"} and cmd.get("selector"):
        return "click_selector"
    return action


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

    if action in {"click", "click_cell", "click_cell_by_index", "clickcell", "select_cell_by_index", "selectcellbyindex"}:
        mapped["type"] = "CLICK_CELL_BY_INDEX" if action.startswith("click") else "SELECT_CELL_BY_INDEX"
        mapped["tunnel"] = "click_cell" if mapped["type"] == "CLICK_CELL_BY_INDEX" else "select_cell_by_index"
        cell_index = cmd.get("cellIndex")
        if cell_index is None:
            cell_index = cmd.get("cell_index")
        if cell_index is None:
            cell_index = cmd.get("index")
        mapped["cellIndex"] = cell_index
        mapped["scrollIntoView"] = cmd.get("scrollIntoView", True)
        mapped["runCell"] = bool(cmd.get("runCell", False))
        return mapped

    if action == "insert_cell":
        mapped["type"] = "INSERT_CELL"
        mapped["tunnel"] = "insert_cell"
        mapped["direction"] = cmd.get("direction", "below")
        mapped["toMarkdown"] = cmd.get("toMarkdown") is True
        mapped["markdownDelayMs"] = cmd.get("markdownDelayMs")
        if cmd.get("cellIndex") is not None:
            mapped["cellIndex"] = cmd.get("cellIndex")
        elif cmd.get("index") is not None:
            mapped["cellIndex"] = cmd.get("index")
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

    if action == "delete_by_index":
        mapped["type"] = "DELETE_CELL"
        mapped["tunnel"] = "delete_by_index"
        if cmd.get("cellIndex") is not None:
            mapped["cellIndex"] = cmd.get("cellIndex")
        elif cmd.get("index") is not None:
            mapped["cellIndex"] = cmd.get("index")
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


def execute_bot_command_sync(cmd: dict, timeout: float = 12.0) -> dict:
    action = _normalize_action(cmd)
    request_id = str(cmd.get("requestId") or uuid.uuid4())
    cmd = dict(cmd)
    cmd["requestId"] = request_id

    if action in {"creating_markdown_by_index", "creating_markdown"}:
        return run_creating_markdown_flow(cmd, timeout=timeout)

    if action in {"edit_cell_by_index", "edit_cell"}:
        return run_edit_cell_flow(cmd, timeout=timeout)

    if action == "delete_by_index":
        return run_delete_cell_flow(cmd, timeout=timeout)

    if action == "insert_cell":
        return run_insert_cell_flow(cmd, timeout=timeout)

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
            {"ok": False, "error": "cell_index is required"},
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


def _result_ok(event: dict | None) -> bool:
    return bool(event and event.get("ok") and _inner_ok(event))


def run_insert_cell_flow(cmd: dict, timeout: float = 12.0) -> dict:
    idx = cmd.get("cellIndex") if cmd.get("cellIndex") is not None else cmd.get("cell_index") or cmd.get("index")
    direction = cmd.get("direction", "below")
    url = _pick_url(cmd)

    if idx is not None:
        click_cmd = dict(cmd)
        click_cmd["action"] = "click"
        click_cmd["cellIndex"] = idx
        click_cmd["requestId"] = str(uuid.uuid4())
        click_event = execute_bot_command_sync(click_cmd, timeout=timeout)
        if not _result_ok(click_event):
            return click_event
        tab_id = _pick_tab_id(cmd) or (click_event.get("result") or {}).get("tabId") or click_event.get("tabId")
    else:
        tab_id = _pick_tab_id(cmd)

    insert_cmd = {
        "action": "insert_cell",
        "requestId": str(uuid.uuid4()),
        "direction": direction,
        "url": url,
    }
    if idx is not None:
        insert_cmd["cellIndex"] = idx
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

    if not waiter.wait(max(0.5, float(timeout))):
        with _PENDING_LOCK:
            _PENDING.pop(request_id, None)
        return build_result_event(cmd, False, {"ok": False, "error": "timeout"}, "timeout")

    with _PENDING_LOCK:
        slot = _PENDING.pop(request_id, None)
    event = (slot or {}).get("event")
    if isinstance(event, dict) and event.get("ok"):
        _sync_persistence("insert_cell", {**cmd, "index": idx, "direction": direction}, event.get("result") or {})
    return event if isinstance(event, dict) else build_result_event(cmd, False, {"ok": False, "error": "no response"}, "no response")


def run_delete_cell_flow(cmd: dict, timeout: float = 12.0) -> dict:
    idx = cmd.get("cellIndex") if cmd.get("cellIndex") is not None else cmd.get("cell_index") or cmd.get("index")
    if idx is None:
        return build_result_event(cmd, False, {"ok": False, "error": "cell_index is required"}, "cell_index is required")

    click_cmd = dict(cmd)
    click_cmd["action"] = "click"
    click_cmd["cellIndex"] = idx
    click_cmd["requestId"] = str(uuid.uuid4())
    click_event = execute_bot_command_sync(click_cmd, timeout=timeout)
    if not _result_ok(click_event):
        return click_event

    url = _pick_url(cmd)
    tab_id = _pick_tab_id(cmd) or (click_event.get("result") or {}).get("tabId") or click_event.get("tabId")

    candidates = [
        f'[data-windowed-list-index="{idx}"] div > div > div > button.cell-context-menu-icon-button.delete',
        f'[data-windowed-list-index="{idx}"] button[aria-label*="Delete"]',
        f'[data-windowed-list-index="{idx}"] .cell-context-menu-icon-button.delete',
        "button.cell-context-menu-icon-button.delete",
        'button[aria-label*="Delete"]',
    ]
    for selector in candidates:
        sel_cmd = {
            "action": "click_selector",
            "requestId": str(uuid.uuid4()),
            "selector": selector,
            "url": url,
        }
        if isinstance(tab_id, int):
            sel_cmd["tabId"] = tab_id
        sel_event = execute_bot_command_sync(sel_cmd, timeout=timeout)
        if _result_ok(sel_event):
            return build_result_event(cmd, True, {"ok": True, "phase": "deleted", "cellIndex": idx, "strategy": "selector"})

    for key in ("d", "d"):
        key_cmd = {
            "action": "send_key",
            "requestId": str(uuid.uuid4()),
            "key": key,
            "url": url,
        }
        if isinstance(tab_id, int):
            key_cmd["tabId"] = tab_id
        key_event = execute_bot_command_sync(key_cmd, timeout=timeout)
        if not _result_ok(key_event):
            return key_event
        time.sleep(0.06)

    return build_result_event(cmd, True, {"ok": True, "phase": "deleted", "cellIndex": idx, "strategy": "dd_keys"})


def run_creating_markdown_flow(cmd: dict, timeout: float = 12.0) -> dict:
    idx = cmd.get("cellIndex") if cmd.get("cellIndex") is not None else cmd.get("cell_index") or cmd.get("index")
    if idx is None:
        return build_result_event(cmd, False, {"ok": False, "error": "index is required"}, "index is required")

    url = _pick_url(cmd)
    tab_id = _pick_tab_id(cmd)

    click_cmd = {"action": "click", "requestId": str(uuid.uuid4()), "cellIndex": idx, "url": url, "runCell": False}
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
        "cellIndex": idx,
    }
    if isinstance(tab_id, int):
        insert_cmd["tabId"] = tab_id
    insert_event = execute_bot_command_sync(insert_cmd, timeout=timeout)
    if not _result_ok(insert_event):
        return insert_event

    time.sleep(0.5)
    focus_cmd = {"action": "click", "requestId": str(uuid.uuid4()), "cellIndex": idx, "url": url}
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
        {"ok": True, "phase": "markdown_created", "cellIndex": idx},
    )


def run_edit_cell_flow(cmd: dict, timeout: float = 12.0) -> dict:
    idx = cmd.get("cellIndex") if cmd.get("cellIndex") is not None else cmd.get("cell_index") or cmd.get("index")
    content = cmd.get("content") or cmd.get("input") or ""
    if idx is None:
        return build_result_event(cmd, False, {"ok": False, "error": "cell_index is required"}, "cell_index is required")

    url = _pick_url(cmd)
    click_cmd = dict(cmd)
    click_cmd["action"] = "click"
    click_cmd["cellIndex"] = idx
    click_cmd["requestId"] = str(uuid.uuid4())
    click_event = execute_bot_command_sync(click_cmd, timeout=timeout)
    if not _result_ok(click_event):
        return click_event

    tab_id = _pick_tab_id(cmd) or (click_event.get("result") or {}).get("tabId") or click_event.get("tabId")
    selector = f'[data-windowed-list-index="{idx}"] .jp-InputArea-editor .cm-content'
    sel_cmd = {
        "action": "click_selector",
        "requestId": str(uuid.uuid4()),
        "selector": selector,
        "url": url,
    }
    if isinstance(tab_id, int):
        sel_cmd["tabId"] = tab_id
    sel_event = execute_bot_command_sync(sel_cmd, timeout=timeout)
    if not _result_ok(sel_event):
        enter_cmd = {"action": "send_key", "requestId": str(uuid.uuid4()), "key": "Enter", "url": url}
        if isinstance(tab_id, int):
            enter_cmd["tabId"] = tab_id
        enter_event = execute_bot_command_sync(enter_cmd, timeout=timeout)
        if not _result_ok(enter_event):
            return enter_event
        sel_event = execute_bot_command_sync(sel_cmd, timeout=timeout)
        if not _result_ok(sel_event):
            return sel_event

    if content:
        from .tool_registry import sync_persistence_for_action

        sync_persistence_for_action(
            "edit_cell_by_index",
            {"url": url, "cell_index": idx, "content": content},
            {"ok": True},
        )

    return build_result_event(
        cmd,
        True,
        {"ok": True, "phase": "edited", "cellIndex": idx, "content": content},
    )
