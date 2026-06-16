"""insert_and_edit_cell tool — insert a code cell below anchor and fill it."""

from __future__ import annotations

import uuid

try:
    from .bot_tool_utils import (
        BROWSER_CLICK_MAX_WAIT_MS,
        BROWSER_CLICK_TIMEOUT_SEC,
        BROWSER_COMPOSITE_TIMEOUT_SEC,
        BROWSER_EDIT_MAX_WAIT_MS,
        BROWSER_EDIT_TIMEOUT_SEC,
        BROWSER_INSERT_TIMEOUT_SEC,
        normalize_insert_and_edit_args,
    )
    from .browser_tool_response import tool_failure, tool_success
    from .cell_index import dom_to_app
except Exception:
    from bot_tool_utils import (
        BROWSER_CLICK_MAX_WAIT_MS,
        BROWSER_CLICK_TIMEOUT_SEC,
        BROWSER_COMPOSITE_TIMEOUT_SEC,
        BROWSER_EDIT_MAX_WAIT_MS,
        BROWSER_EDIT_TIMEOUT_SEC,
        BROWSER_INSERT_TIMEOUT_SEC,
        normalize_insert_and_edit_args,
    )
    from browser_tool_response import tool_failure, tool_success
    from cell_index import dom_to_app

TOOL = "insert_and_edit_cell"


def run_insert_and_edit_cell(args: dict) -> dict:
    cmd, err = normalize_insert_and_edit_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command

    attempt_cmd = dict(cmd)
    attempt_cmd["requestId"] = str(uuid.uuid4())
    attempt_cmd["timeout"] = BROWSER_COMPOSITE_TIMEOUT_SEC
    attempt_cmd["maxWaitMs"] = BROWSER_CLICK_MAX_WAIT_MS
    attempt_cmd["click_timeout"] = BROWSER_CLICK_TIMEOUT_SEC
    attempt_cmd["insert_timeout"] = BROWSER_INSERT_TIMEOUT_SEC
    attempt_cmd["edit_timeout"] = BROWSER_EDIT_TIMEOUT_SEC

    event = execute_bot_command(attempt_cmd, timeout=BROWSER_COMPOSITE_TIMEOUT_SEC)

    if event.get("ok"):
        inner = event.get("result") if isinstance(event.get("result"), dict) else {}
        anchor_dom = inner.get("insertedBelow", cmd.get("dom_index"))
        new_dom = inner.get("newDomIndex")
        anchor_app = dom_to_app(int(anchor_dom)) if anchor_dom is not None else cmd.get("app_index")
        new_app = dom_to_app(int(new_dom)) if new_dom is not None else None
        return tool_success(
            TOOL,
            anchor_cell_index=anchor_app,
            new_cell_index=new_app,
            cell_index=new_app,
            anchor_dom_index=anchor_dom,
            new_dom_index=new_dom,
            dom_index=new_dom,
            app_index=new_app,
            chars=inner.get("chars") or len(str(cmd.get("content") or "")),
            phase=inner.get("phase") or "insert_code_below_complete",
            direction=cmd.get("direction", "below"),
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
        anchor_cell_index=cmd.get("app_index"),
        anchor_dom_index=cmd.get("dom_index"),
    )
