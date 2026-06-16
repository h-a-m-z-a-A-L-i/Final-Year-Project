"""edit_cell_by_index tool — select cell and replace editor content."""

from __future__ import annotations

import uuid

try:
    from .bot_tool_utils import (
        BROWSER_EDIT_MAX_WAIT_MS,
        BROWSER_EDIT_TIMEOUT_SEC,
        normalize_edit_cell_args,
    )
    from .browser_tool_response import tool_failure, tool_success
    from .cell_index import dom_to_app
except Exception:
    from bot_tool_utils import (
        BROWSER_EDIT_MAX_WAIT_MS,
        BROWSER_EDIT_TIMEOUT_SEC,
        normalize_edit_cell_args,
    )
    from browser_tool_response import tool_failure, tool_success
    from cell_index import dom_to_app

TOOL = "edit_cell_by_index"


def run_edit_cell(args: dict) -> dict:
    cmd, err = normalize_edit_cell_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command

    attempt_cmd = dict(cmd)
    attempt_cmd["requestId"] = str(uuid.uuid4())
    attempt_cmd["timeout"] = BROWSER_EDIT_TIMEOUT_SEC
    attempt_cmd["maxWaitMs"] = BROWSER_EDIT_MAX_WAIT_MS

    event = execute_bot_command(attempt_cmd, timeout=BROWSER_EDIT_TIMEOUT_SEC)

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
            chars=inner.get("chars") or len(str(cmd.get("content") or "")),
            strategy=inner.get("strategy"),
            phase=inner.get("phase") or "content_set",
        )

    return tool_failure(
        TOOL,
        str(event.get("error") or (event.get("result") or {}).get("error") or f"{TOOL} failed"),
        cmd=cmd,
        event=event,
    )
