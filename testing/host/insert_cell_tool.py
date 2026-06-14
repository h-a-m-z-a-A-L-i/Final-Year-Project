"""insert_cell tool — insert an empty code cell above/below anchor."""

from __future__ import annotations

import uuid

try:
    from .bot_tool_utils import (
        BROWSER_INSERT_MAX_WAIT_MS,
        BROWSER_INSERT_TIMEOUT_SEC,
        normalize_insert_cell_args,
    )
    from .browser_tool_response import tool_failure, tool_success
    from .cell_index import dom_to_app
except Exception:
    from bot_tool_utils import (
        BROWSER_INSERT_MAX_WAIT_MS,
        BROWSER_INSERT_TIMEOUT_SEC,
        normalize_insert_cell_args,
    )
    from browser_tool_response import tool_failure, tool_success
    from cell_index import dom_to_app

TOOL = "insert_cell"


def run_insert_cell(args: dict) -> dict:
    cmd, err = normalize_insert_cell_args(args)
    if err:
        return err

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command

    attempt_cmd = dict(cmd)
    attempt_cmd["requestId"] = str(uuid.uuid4())
    attempt_cmd["timeout"] = BROWSER_INSERT_TIMEOUT_SEC
    attempt_cmd["maxWaitMs"] = BROWSER_INSERT_MAX_WAIT_MS

    event = execute_bot_command(attempt_cmd, timeout=BROWSER_INSERT_TIMEOUT_SEC)

    if event.get("ok"):
        inner = event.get("result") if isinstance(event.get("result"), dict) else {}
        anchor_dom = inner.get("insertedBelow", cmd.get("dom_index"))
        new_dom = inner.get("newDomIndex") or inner.get("domIndex")
        anchor_app = dom_to_app(int(anchor_dom)) if anchor_dom is not None else cmd.get("app_index")
        new_app = dom_to_app(int(new_dom)) if new_dom is not None else None
        return tool_success(
            TOOL,
            anchor_cell_index=anchor_app,
            anchor_dom_index=anchor_dom,
            new_cell_index=new_app,
            new_dom_index=new_dom,
            cell_index=new_app,
            dom_index=new_dom,
            direction=cmd.get("direction", "below"),
            phase=inner.get("phase") or "insert_complete",
            strategy=inner.get("strategy"),
        )

    return tool_failure(
        TOOL,
        str(event.get("error") or (event.get("result") or {}).get("error") or f"{TOOL} failed"),
        cmd=cmd,
        event=event,
        direction=cmd.get("direction"),
    )
