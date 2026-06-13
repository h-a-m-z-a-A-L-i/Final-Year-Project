"""click_cell tool — validation and single-attempt browser execution."""

from __future__ import annotations

import uuid

try:
    from .bot_tool_utils import (
        BROWSER_CLICK_MAX_WAIT_MS,
        BROWSER_CLICK_TIMEOUT_SEC,
        normalize_click_cell_args,
    )
    from .browser_tool_response import tool_failure, tool_success
    from .cell_index import dom_to_app
except Exception:
    from bot_tool_utils import (
        BROWSER_CLICK_MAX_WAIT_MS,
        BROWSER_CLICK_TIMEOUT_SEC,
        normalize_click_cell_args,
    )
    from browser_tool_response import tool_failure, tool_success
    from cell_index import dom_to_app

TOOL = "click_cell"


def run_click_cell(args: dict) -> dict:
    cmd, err = normalize_click_cell_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command

    attempt_cmd = dict(cmd)
    attempt_cmd["requestId"] = str(uuid.uuid4())
    attempt_cmd["timeout"] = BROWSER_CLICK_TIMEOUT_SEC
    attempt_cmd["maxWaitMs"] = BROWSER_CLICK_MAX_WAIT_MS

    event = execute_bot_command(attempt_cmd, timeout=BROWSER_CLICK_TIMEOUT_SEC)

    if event.get("ok"):
        inner = event.get("result") if isinstance(event.get("result"), dict) else {}
        dom_index = inner.get("domIndex")
        if dom_index is None:
            dom_index = cmd.get("dom_index")
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
            data_windowed_list_index=inner.get("dataWindowedListIndex"),
            strategy=inner.get("strategy"),
            run_cell=bool(cmd.get("runCell")),
        )

    return tool_failure(
        TOOL,
        str(event.get("error") or (event.get("result") or {}).get("error") or f"{TOOL} failed"),
        cmd=cmd,
        event=event,
    )
