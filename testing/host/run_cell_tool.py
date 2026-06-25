"""run_cell tool — dispatch run in browser (fire-and-forget)."""

from __future__ import annotations

import time

try:
    from .bot_tool_utils import BROWSER_RUN_MAX_WAIT_MS, normalize_run_cell_args, pick_notebook_url
    from .browser_tool_response import tool_failure, tool_success
    from .run_cell_verification import RUN_DISPATCH_TIMEOUT_SEC, dispatch_run_cell
except Exception:
    from bot_tool_utils import BROWSER_RUN_MAX_WAIT_MS, normalize_run_cell_args, pick_notebook_url
    from browser_tool_response import tool_failure, tool_success
    from run_cell_verification import RUN_DISPATCH_TIMEOUT_SEC, dispatch_run_cell

TOOL = "run_cell"


def run_run_cell(args: dict) -> dict:
    cmd, err = normalize_run_cell_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    url = pick_notebook_url(cmd) or pick_notebook_url(args)
    if not url:
        return tool_failure(TOOL, "url is required")

    cell_index = cmd.get("app_index") or cmd.get("cell_index")
    if cell_index is None:
        return tool_failure(TOOL, "cell_index is required")

    dom_index = cmd.get("dom_index")
    app_index = int(cell_index)
    dispatch_timeout = float(args.get("dispatch_timeout") or RUN_DISPATCH_TIMEOUT_SEC)
    started = time.monotonic()

    attempt_cmd = dict(cmd)
    attempt_cmd["maxWaitMs"] = BROWSER_RUN_MAX_WAIT_MS
    dispatch = dispatch_run_cell(attempt_cmd, timeout=dispatch_timeout)
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)

    if not dispatch.get("ok"):
        error = str(dispatch.get("error") or "run dispatch failed")
        inner = dispatch.get("result") if isinstance(dispatch.get("result"), dict) else {}
        inner_err = str(inner.get("error") or "")
        if inner_err:
            error = f"{error} ({inner_err})"
        return tool_failure(
            TOOL,
            error,
            cmd=cmd,
            cell_index=app_index,
            dom_index=dom_index,
            phase="run_dispatch_failed",
            dispatch_time_ms=elapsed_ms,
        )

    return tool_success(
        TOOL,
        cell_index=app_index,
        dom_index=dom_index,
        app_index=app_index,
        phase="dispatched",
        dispatched=True,
        dispatch_time_ms=elapsed_ms,
    )
