"""insert_cell tool — dispatch select + toolbar click (no host verification wait)."""

from __future__ import annotations

import time

try:
    from .bot_command import execute_bot_command
    from .bot_tool_utils import (
        BROWSER_INSERT_MAX_WAIT_MS,
        normalize_insert_cell_args,
        normalize_select_cell_args,
    )
    from .browser_tool_response import tool_failure, tool_success
except Exception:
    from bot_command import execute_bot_command
    from bot_tool_utils import (
        BROWSER_INSERT_MAX_WAIT_MS,
        normalize_insert_cell_args,
        normalize_select_cell_args,
    )
    from browser_tool_response import tool_failure, tool_success

TOOL = "insert_cell"

INSERT_TOOLBAR_BUTTON_SELECTOR = (
    "#site-content > div.sc-bZIVci.fycqcn > div > div.sc-dZtDeN.kuQWcu > "
    "div > div.sc-jmrxpW.hbTSyO > span:nth-child(1) > button"
)
INSERT_AFTER_SELECT_SEC = 1.0
INSERT_DISPATCH_TIMEOUT_SEC = 2.5


def _dispatch_fire_and_forget(cmd: dict, *, timeout: float = INSERT_DISPATCH_TIMEOUT_SEC) -> dict:
    attempt = dict(cmd)
    attempt["fire_and_forget"] = True
    attempt["wait_for_result"] = False
    return execute_bot_command(attempt, timeout=timeout)


def run_insert_cell(args: dict) -> dict:
    cmd, err = normalize_insert_cell_args(args)
    if err:
        err.setdefault("tool", TOOL)
        return err

    anchor_dom = cmd.get("dom_index")
    anchor_app = cmd.get("app_index") or cmd.get("index") or cmd.get("cell_index")
    if anchor_dom is None or anchor_app is None:
        return tool_failure(TOOL, "index (anchor cell) is required")

    direction = str(cmd.get("direction") or "below").strip().lower()
    if direction != "below":
        return tool_failure(TOOL, "insert_cell only supports direction=below for toolbar insert")

    select_args = dict(args)
    select_args["cell_index"] = anchor_app
    select_args["index"] = anchor_app
    select_cmd, select_err = normalize_select_cell_args(select_args)
    if select_err:
        select_err.setdefault("tool", TOOL)
        return select_err

    dispatch_timeout = float(args.get("dispatch_timeout") or INSERT_DISPATCH_TIMEOUT_SEC)
    started = time.monotonic()

    select_cmd["maxWaitMs"] = BROWSER_INSERT_MAX_WAIT_MS
    select_event = _dispatch_fire_and_forget(select_cmd, timeout=dispatch_timeout)
    if not select_event.get("ok"):
        error = str(select_event.get("error") or "select dispatch failed")
        return tool_failure(
            TOOL,
            error,
            cmd=cmd,
            anchor_cell_index=int(anchor_app),
            anchor_dom_index=int(anchor_dom),
            direction=direction,
            phase="select_dispatch_failed",
        )

    time.sleep(float(args.get("settle_sec") or INSERT_AFTER_SELECT_SEC))

    url = cmd.get("url")
    tab_id = cmd.get("tabId")
    click_cmd = {
        "action": "click_selector",
        "selector": INSERT_TOOLBAR_BUTTON_SELECTOR,
        "url": url,
    }
    if tab_id is not None:
        click_cmd["tabId"] = tab_id
    click_event = _dispatch_fire_and_forget(click_cmd, timeout=dispatch_timeout)

    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
    if not (click_event or {}).get("ok"):
        error = str((click_event or {}).get("error") or "toolbar click dispatch failed")
        return tool_failure(
            TOOL,
            error,
            cmd=cmd,
            anchor_cell_index=int(anchor_app),
            anchor_dom_index=int(anchor_dom),
            direction=direction,
            phase="toolbar_dispatch_failed",
            duration_ms=elapsed_ms,
        )

    return tool_success(
        TOOL,
        anchor_cell_index=int(anchor_app),
        anchor_dom_index=int(anchor_dom),
        new_cell_index=int(anchor_app) + 1,
        new_dom_index=int(anchor_dom) + 1,
        direction=direction,
        phase="dispatched",
        dispatched=True,
        duration_ms=elapsed_ms,
    )
