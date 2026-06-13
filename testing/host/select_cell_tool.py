"""select_cell_by_index tool — focus a cell without running it."""

from __future__ import annotations

import uuid

try:
    from .bot_tool_utils import (
        BROWSER_SELECT_MAX_WAIT_MS,
        BROWSER_SELECT_TIMEOUT_SEC,
        normalize_select_cell_args,
    )
    from .browser_tool_response import tool_failure, tool_success
    from .cell_index import dom_to_app
except Exception:
    from bot_tool_utils import (
        BROWSER_SELECT_MAX_WAIT_MS,
        BROWSER_SELECT_TIMEOUT_SEC,
        normalize_select_cell_args,
    )
    from browser_tool_response import tool_failure, tool_success
    from cell_index import dom_to_app

TOOL = "select_cell_by_index"


def run_select_cell(args: dict) -> dict:
    cmd, err = normalize_select_cell_args(args)
    if err:
        return err

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command

    attempt_cmd = dict(cmd)
    attempt_cmd["requestId"] = str(uuid.uuid4())
    attempt_cmd["timeout"] = BROWSER_SELECT_TIMEOUT_SEC
    attempt_cmd["maxWaitMs"] = BROWSER_SELECT_MAX_WAIT_MS

    event = execute_bot_command(attempt_cmd, timeout=BROWSER_SELECT_TIMEOUT_SEC)
    if event.get("ok"):
        inner = event.get("result") if isinstance(event.get("result"), dict) else {}
        dom_index = inner.get("domIndex", cmd.get("dom_index"))
        app_index = inner.get("appIndex")
        if app_index is None and dom_index is not None:
            app_index = dom_to_app(int(dom_index))
        if app_index is None:
            app_index = cmd.get("app_index")
        return tool_success(
            TOOL,
            cell_index=app_index,
            dom_index=dom_index,
            app_index=app_index,
            strategy=inner.get("strategy"),
            data_windowed_list_index=inner.get("dataWindowedListIndex"),
        )

    return tool_failure(
        TOOL,
        str(event.get("error") or (event.get("result") or {}).get("error") or f"{TOOL} failed"),
        cmd=cmd,
        event=event,
    )
