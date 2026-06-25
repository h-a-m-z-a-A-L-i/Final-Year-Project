"""run_cell tool — dispatch run in browser, verify via execution detector (not extension ack)."""

from __future__ import annotations

import time

try:
    from .bot_tool_utils import BROWSER_RUN_MAX_WAIT_MS, normalize_run_cell_args, pick_notebook_url, pick_tab_id
    from .browser_tool_response import tool_failure, tool_success
    from .run_cell_verification import (
        RUN_DISPATCH_TIMEOUT_SEC,
        RUN_VERIFY_TIMEOUT_SEC,
        capture_run_baseline,
        dispatch_run_cell,
        wait_for_run_verification,
    )
except Exception:
    from bot_tool_utils import BROWSER_RUN_MAX_WAIT_MS, normalize_run_cell_args, pick_notebook_url, pick_tab_id
    from browser_tool_response import tool_failure, tool_success
    from run_cell_verification import (
        RUN_DISPATCH_TIMEOUT_SEC,
        RUN_VERIFY_TIMEOUT_SEC,
        capture_run_baseline,
        dispatch_run_cell,
        wait_for_run_verification,
    )

TOOL = "run_cell"


def run_run_cell(args: dict) -> dict:
    cmd, err = normalize_run_cell_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    url = pick_notebook_url(cmd) or pick_notebook_url(args)
    if not url:
        return tool_failure(TOOL, "url is required for run verification")

    cell_index = cmd.get("app_index") or cmd.get("cell_index")
    if cell_index is None:
        return tool_failure(TOOL, "cell_index is required")

    verify_timeout = float(args.get("verify_timeout") or RUN_VERIFY_TIMEOUT_SEC)
    dispatch_timeout = float(args.get("dispatch_timeout") or RUN_DISPATCH_TIMEOUT_SEC)

    baseline = capture_run_baseline(url, int(cell_index))
    dispatch_started = time.monotonic()
    attempt_cmd = dict(cmd)
    attempt_cmd["maxWaitMs"] = BROWSER_RUN_MAX_WAIT_MS
    dispatch = dispatch_run_cell(attempt_cmd, timeout=dispatch_timeout)
    dispatch_time_ms = round((time.monotonic() - dispatch_started) * 1000.0, 1)

    tab_id = pick_tab_id(cmd) or pick_tab_id(args)
    if not tab_id:
        inner = dispatch.get("result") if isinstance(dispatch.get("result"), dict) else {}
        tab_id = dispatch.get("tabId") or inner.get("tabId")
        if not isinstance(tab_id, int):
            try:
                from .browser_target_context import resolve_tab_id_for_url
            except Exception:
                from browser_target_context import resolve_tab_id_for_url  # type: ignore
            tab_id = resolve_tab_id_for_url(url)

    wait = wait_for_run_verification(
        url=url,
        cell_index=int(cell_index),
        before_data=baseline.get("snapshot"),
        before_cell=baseline.get("before_cell"),
        host_log_offset=int(baseline.get("host_log_offset") or 0),
        timeout=verify_timeout,
        started_at=dispatch_started,
        tab_id=tab_id if isinstance(tab_id, int) else None,
    )

    dom_index = cmd.get("dom_index")
    app_index = int(cell_index)

    if wait.get("run_verified"):
        return tool_success(
            TOOL,
            cell_index=app_index,
            dom_index=dom_index,
            app_index=app_index,
            run_verified=True,
            wait_reason=wait.get("wait_reason"),
            execution_order=wait.get("execution_order"),
            execution_title=wait.get("execution_title"),
            execution_time_sec=wait.get("execution_time_sec"),
            dispatched=bool(dispatch.get("ok")),
            dispatch_time_ms=dispatch_time_ms,
        )

    error = str(wait.get("error") or "run not verified")
    if not dispatch.get("ok"):
        dispatch_err = str(dispatch.get("error") or (dispatch.get("result") or {}).get("error") or "")
        if dispatch_err:
            error = f"{error} (dispatch: {dispatch_err})"
    return tool_failure(
        TOOL,
        error,
        cmd=cmd,
        event=wait,
        cell_index=app_index,
        dom_index=dom_index,
        run_verified=False,
        dispatch_ok=bool(dispatch.get("ok")),
    )
