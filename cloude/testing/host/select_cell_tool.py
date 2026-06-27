"""select_cell_by_index tool — dispatch select in browser (fire-and-forget)."""

from __future__ import annotations

import time

try:
    from .bot_tool_utils import (
        BROWSER_SELECT_MAX_WAIT_MS,
        BROWSER_SELECT_TIMEOUT_SEC,
        normalize_select_cell_args,
        pick_notebook_url,
    )
    from .browser_tool_response import tool_failure, tool_success
    from .select_cell_verification import SELECT_DISPATCH_TIMEOUT_SEC, dispatch_select_cell
except Exception:
    from bot_tool_utils import (
        BROWSER_SELECT_MAX_WAIT_MS,
        BROWSER_SELECT_TIMEOUT_SEC,
        normalize_select_cell_args,
        pick_notebook_url,
    )
    from browser_tool_response import tool_failure, tool_success
    from select_cell_verification import SELECT_DISPATCH_TIMEOUT_SEC, dispatch_select_cell

TOOL = "select_cell_by_index"


def run_select_cell(args: dict) -> dict:
    cmd, err = normalize_select_cell_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    url = pick_notebook_url(cmd) or pick_notebook_url(args)
    if not url:
        return tool_failure(TOOL, "url is required")

    dom_index = cmd.get("dom_index")
    app_index = cmd.get("app_index") or cmd.get("cell_index")
    if dom_index is None or app_index is None:
        return tool_failure(TOOL, "cell_index is required")

    dispatch_timeout = float(args.get("dispatch_timeout") or SELECT_DISPATCH_TIMEOUT_SEC)
    started = time.monotonic()

    attempt_cmd = dict(cmd)
    attempt_cmd["maxWaitMs"] = BROWSER_SELECT_MAX_WAIT_MS
    attempt_cmd["timeout"] = BROWSER_SELECT_TIMEOUT_SEC
    dispatch = dispatch_select_cell(attempt_cmd, timeout=dispatch_timeout)
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)

    if not dispatch.get("ok"):
        error = str(dispatch.get("error") or "select dispatch failed")
        inner = dispatch.get("result") if isinstance(dispatch.get("result"), dict) else {}
        inner_err = str(inner.get("error") or "")
        if inner_err:
            error = f"{error} ({inner_err})"
        return tool_failure(
            TOOL,
            error,
            cmd=cmd,
            cell_index=int(app_index),
            dom_index=int(dom_index),
            phase="select_dispatch_failed",
            dispatch_time_ms=elapsed_ms,
        )

    return tool_success(
        TOOL,
        cell_index=int(app_index),
        dom_index=int(dom_index),
        app_index=int(app_index),
        phase="dispatched",
        dispatched=True,
        dispatch_time_ms=elapsed_ms,
    )
