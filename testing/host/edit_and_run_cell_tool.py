"""edit_and_run_cell tool — replace cell source then execute it."""

from __future__ import annotations

import uuid

try:
    from .bot_tool_utils import (
        BROWSER_EDIT_AND_RUN_TIMEOUT_SEC,
        BROWSER_EDIT_MAX_WAIT_MS,
        BROWSER_EDIT_TIMEOUT_SEC,
        BROWSER_RUN_MAX_WAIT_MS,
        BROWSER_RUN_TIMEOUT_SEC,
        normalize_edit_and_run_args,
    )
    from .browser_tool_response import tool_failure, tool_success
    from .cell_index import dom_to_app
except Exception:
    from bot_tool_utils import (
        BROWSER_EDIT_AND_RUN_TIMEOUT_SEC,
        BROWSER_EDIT_MAX_WAIT_MS,
        BROWSER_EDIT_TIMEOUT_SEC,
        BROWSER_RUN_MAX_WAIT_MS,
        BROWSER_RUN_TIMEOUT_SEC,
        normalize_edit_and_run_args,
    )
    from browser_tool_response import tool_failure, tool_success
    from cell_index import dom_to_app

TOOL = "edit_and_run_cell"


def run_edit_and_run_cell(args: dict) -> dict:
    cmd, err = normalize_edit_and_run_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command

    attempt_cmd = dict(cmd)
    attempt_cmd["requestId"] = str(uuid.uuid4())
    attempt_cmd["timeout"] = BROWSER_EDIT_AND_RUN_TIMEOUT_SEC
    attempt_cmd["maxWaitMs"] = BROWSER_EDIT_MAX_WAIT_MS
    attempt_cmd["run_maxWaitMs"] = BROWSER_RUN_MAX_WAIT_MS
    attempt_cmd["edit_timeout"] = BROWSER_EDIT_TIMEOUT_SEC
    attempt_cmd["run_timeout"] = BROWSER_RUN_TIMEOUT_SEC

    event = execute_bot_command(attempt_cmd, timeout=BROWSER_EDIT_AND_RUN_TIMEOUT_SEC)

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
            phase=inner.get("phase") or "edit_and_run_complete",
            chars=inner.get("chars") or len(str(cmd.get("content") or "")),
            edit_strategy=inner.get("edit_strategy"),
            run_strategy=inner.get("run_strategy"),
            command_id=inner.get("command_id"),
        )

    return tool_failure(
        TOOL,
        str(
            event.get("error")
            or (event.get("result") or {}).get("error")
            or f"{TOOL} failed"
        ),
        cmd=cmd,
        event=event,
    )
