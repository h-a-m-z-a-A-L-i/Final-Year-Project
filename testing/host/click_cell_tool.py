"""click_cell tool — select_cell_by_index then Enter (same as smoke_select + key)."""

from __future__ import annotations

import time
import uuid

try:
    from .bot_tool_utils import BROWSER_CLICK_TIMEOUT_SEC, normalize_select_cell_args
    from .browser_tool_response import tool_failure, tool_success
    from .cell_index import dom_to_app
except Exception:
    from bot_tool_utils import BROWSER_CLICK_TIMEOUT_SEC, normalize_select_cell_args
    from browser_tool_response import tool_failure, tool_success
    from cell_index import dom_to_app

TOOL = "click_cell"
ENTER_DELAY_SEC = 0.35


def run_click_cell(args: dict) -> dict:
    select_cmd, err = normalize_select_cell_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command

    select_attempt = dict(select_cmd)
    select_attempt["requestId"] = str(uuid.uuid4())
    select_attempt["timeout"] = BROWSER_CLICK_TIMEOUT_SEC

    select_event = execute_bot_command(select_attempt, timeout=BROWSER_CLICK_TIMEOUT_SEC)
    if not select_event.get("ok"):
        return tool_failure(
            TOOL,
            str(select_event.get("error") or "select step failed"),
            cmd=select_cmd,
            event=select_event,
        )

    time.sleep(ENTER_DELAY_SEC)

    enter_cmd = {
        "action": "send_key",
        "url": select_cmd["url"],
        "key": "Enter",
        "requestId": str(uuid.uuid4()),
        "timeout": BROWSER_CLICK_TIMEOUT_SEC,
    }
    tab_id = select_cmd.get("tabId") or select_cmd.get("tab_id")
    if isinstance(tab_id, int):
        enter_cmd["tabId"] = tab_id
        enter_cmd["tab_id"] = tab_id

    enter_event = execute_bot_command(enter_cmd, timeout=BROWSER_CLICK_TIMEOUT_SEC)
    if not enter_event.get("ok"):
        return tool_failure(
            TOOL,
            str(enter_event.get("error") or "Enter key step failed"),
            cmd=select_cmd,
            event=enter_event,
        )

    inner = select_event.get("result") if isinstance(select_event.get("result"), dict) else {}
    dom_index = inner.get("domIndex", select_cmd.get("dom_index"))
    app_index = inner.get("appIndex")
    if app_index is None and dom_index is not None:
        app_index = dom_to_app(int(dom_index))
    if app_index is None:
        app_index = select_cmd.get("app_index")

    return tool_success(
        TOOL,
        cell_index=app_index,
        dom_index=dom_index,
        app_index=app_index,
        data_windowed_list_index=inner.get("dataWindowedListIndex"),
        strategy="select-then-enter",
        phase="clicked",
        dispatched=inner.get("dispatched"),
    )
