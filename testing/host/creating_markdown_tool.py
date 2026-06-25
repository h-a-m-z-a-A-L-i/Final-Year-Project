"""creating_markdown_by_index — select anchor, then footer shadow-DOM markdown click.

FROZEN — verified working. Flow:
  1. select_cell_by_index (fire-and-forget)
  2. sleep 1s
  3. click_cell_markdown_button → footer.shadowRoot markdown btn
Do not change without explicit request.
"""

from __future__ import annotations

import time

try:
    from .bot_command import execute_bot_command
    from .bot_tool_utils import (
        BROWSER_SELECT_MAX_WAIT_MS,
        normalize_creating_markdown_args,
        normalize_select_cell_args,
    )
    from .browser_tool_response import tool_failure, tool_success
except Exception:
    from bot_command import execute_bot_command
    from bot_tool_utils import (
        BROWSER_SELECT_MAX_WAIT_MS,
        normalize_creating_markdown_args,
        normalize_select_cell_args,
    )
    from browser_tool_response import tool_failure, tool_success

TOOL = "creating_markdown_by_index"

MARKDOWN_AFTER_SELECT_SEC = 1.0
MARKDOWN_DISPATCH_TIMEOUT_SEC = 2.5


def _dispatch_fire_and_forget(cmd: dict, *, timeout: float = MARKDOWN_DISPATCH_TIMEOUT_SEC) -> dict:
    attempt = dict(cmd)
    attempt["fire_and_forget"] = True
    attempt["wait_for_result"] = False
    return execute_bot_command(attempt, timeout=timeout)


def run_creating_markdown(args: dict) -> dict:
    cmd, err = normalize_creating_markdown_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    dom_index = cmd.get("dom_index")
    app_index = cmd.get("app_index") or cmd.get("index") or cmd.get("cell_index")
    if dom_index is None or app_index is None:
        return tool_failure(TOOL, "index (anchor cell) is required")

    select_args = dict(args)
    select_args["cell_index"] = app_index
    select_args["index"] = app_index
    select_cmd, select_err = normalize_select_cell_args(select_args)
    if select_err:
        select_err.setdefault("tool", TOOL)
        return select_err

    dispatch_timeout = float(args.get("dispatch_timeout") or MARKDOWN_DISPATCH_TIMEOUT_SEC)
    started = time.monotonic()

    select_cmd["maxWaitMs"] = BROWSER_SELECT_MAX_WAIT_MS
    select_event = _dispatch_fire_and_forget(select_cmd, timeout=dispatch_timeout)
    if not select_event.get("ok"):
        error = str(select_event.get("error") or "select dispatch failed")
        return tool_failure(
            TOOL,
            error,
            cmd=cmd,
            cell_index=int(app_index),
            dom_index=int(dom_index),
            phase="select_dispatch_failed",
        )

    time.sleep(float(args.get("settle_sec") or MARKDOWN_AFTER_SELECT_SEC))

    url = cmd.get("url")
    tab_id = cmd.get("tabId")
    click_cmd = {
        "action": "click_cell_markdown_button",
        "url": url,
    }
    if tab_id is not None:
        click_cmd["tabId"] = tab_id
    click_event = _dispatch_fire_and_forget(click_cmd, timeout=dispatch_timeout)

    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
    if not (click_event or {}).get("ok"):
        error = str((click_event or {}).get("error") or "markdown click dispatch failed")
        return tool_failure(
            TOOL,
            error,
            cmd=cmd,
            cell_index=int(app_index),
            dom_index=int(dom_index),
            phase="markdown_dispatch_failed",
            duration_ms=elapsed_ms,
        )

    return tool_success(
        TOOL,
        cell_index=int(app_index),
        dom_index=int(dom_index),
        app_index=int(app_index),
        phase="dispatched",
        dispatched=True,
        duration_ms=elapsed_ms,
    )
