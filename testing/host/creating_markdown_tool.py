"""creating_markdown_by_index tool — insert markdown cell above anchor."""

from __future__ import annotations

import uuid

try:
    from .bot_tool_utils import BROWSER_MARKDOWN_TIMEOUT_SEC, normalize_creating_markdown_args
    from .browser_tool_response import tool_failure, tool_success
    from .cell_index import dom_to_app
except Exception:
    from bot_tool_utils import BROWSER_MARKDOWN_TIMEOUT_SEC, normalize_creating_markdown_args
    from browser_tool_response import tool_failure, tool_success
    from cell_index import dom_to_app

TOOL = "creating_markdown_by_index"


def run_creating_markdown(args: dict) -> dict:
    cmd, err = normalize_creating_markdown_args(args)
    if err:
        return err

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command

    attempt_cmd = dict(cmd)
    attempt_cmd["requestId"] = str(uuid.uuid4())
    attempt_cmd["timeout"] = BROWSER_MARKDOWN_TIMEOUT_SEC

    event = execute_bot_command(attempt_cmd, timeout=BROWSER_MARKDOWN_TIMEOUT_SEC)

    if event.get("ok"):
        inner = event.get("result") if isinstance(event.get("result"), dict) else {}
        dom_index = inner.get("domIndex", cmd.get("dom_index"))
        app_index = dom_to_app(int(dom_index)) if dom_index is not None else cmd.get("app_index")
        return tool_success(
            TOOL,
            cell_index=app_index,
            dom_index=dom_index,
            app_index=app_index,
            phase=inner.get("phase") or "markdown_created",
        )

    return tool_failure(
        TOOL,
        str(event.get("error") or (event.get("result") or {}).get("error") or f"{TOOL} failed"),
        cmd=cmd,
        event=event,
    )
