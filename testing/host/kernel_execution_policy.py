"""
Kernel-mode execution metadata policy.

OFF — no execution metadata (kernel not running).
ON  — track execution; reset only on OFF→ON; preserve across page reload while ON.
"""

from __future__ import annotations

from typing import Any

SCENARIO_OFF = "scenario_1_new_notebook_off"
SCENARIO_ON = "scenario_2_kernel_on"
SCENARIO_LOADING = "editor_loading"

# Backward-compatible aliases (mapped to ON by normalize_scenario)
SCENARIO_FRESH = SCENARIO_ON
SCENARIO_RELOAD = SCENARIO_ON

TITLE_NOT_EXECUTED = "Cell is not executed yet"
TITLE_PENDING = "Cell execution is pending"
TITLE_RUNNING = "Cell is being executed"

SCENARIO_LABELS = {
    SCENARIO_OFF: "off",
    SCENARIO_ON: "on",
    SCENARIO_LOADING: "loading",
    "unknown": "unknown",
}

_ON_ALIASES = {
    SCENARIO_ON,
    "scenario_2_fresh_kernel_started",
    "scenario_3_reload_running_kernel",
}


def normalize_scenario(kernel_scenario: str) -> str:
    s = str(kernel_scenario or "unknown").strip().lower()
    if s in SCENARIO_LABELS:
        return s
    if s in _ON_ALIASES or "kernel_on" in s:
        return SCENARIO_ON
    if "fresh" in s or "reload" in s:
        return SCENARIO_ON
    if "off" in s:
        return SCENARIO_OFF
    if "loading" in s:
        return SCENARIO_LOADING
    return "unknown"


def scenario_is_off(scenario: str) -> bool:
    return normalize_scenario(scenario) == SCENARIO_OFF


def scenario_is_on(scenario: str) -> bool:
    return normalize_scenario(scenario) == SCENARIO_ON


def scenario_is_fresh(scenario: str) -> bool:
    return scenario_is_on(scenario)


def scenario_is_reload(scenario: str) -> bool:
    return scenario_is_on(scenario)


def fresh_lifecycle_title(
    *,
    execution_status: str,
    execution_order: int | None,
    seen_running: bool,
) -> str:
    status = str(execution_status or "idle").strip().lower()
    if status == "queued":
        return TITLE_PENDING
    if status == "running":
        return TITLE_RUNNING
    if execution_order is not None and (status == "executed" or seen_running):
        return f"Cell executed (Execution #{int(execution_order)})"
    return TITLE_NOT_EXECUTED


def resolve_cell_execution(
    cell: dict[str, Any],
    *,
    kernel_scenario: str,
    previous_revision: dict[str, Any] | None,
    scenario_entered: bool,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """
    Compute persisted cell execution fields + revision tracking row.

    Returns (save_cell_slice, revision_row, should_save_flag).
    """
    scenario = normalize_scenario(kernel_scenario)
    prev = dict(previous_revision or {})
    baseline_order = prev.get("baseline_order")
    seen_running = bool(prev.get("seen_running"))
    previous_title = str(prev.get("title") or "")

    current_order = cell.get("execution_order")
    try:
        current_order = int(current_order) if current_order is not None else None
    except (TypeError, ValueError):
        current_order = None

    current_title = str(cell.get("execution_title") or "").strip()
    execution_status = str(cell.get("execution_status") or "idle").strip().lower()
    is_active = execution_status in {"queued", "running"}
    is_executed = execution_status == "executed"
    should_save = False

    saved_order: int | None = None
    saved_title = ""

    if scenario_is_off(scenario):
        saved_order = None
        saved_title = ""
        baseline_order = None
        seen_running = False
    elif scenario_is_on(scenario) and scenario_entered:
        saved_order = None
        saved_title = TITLE_NOT_EXECUTED
        seen_running = False
        baseline_order = None
    elif scenario_is_on(scenario):
        if is_active:
            saved_order = current_order if current_order is not None else baseline_order
            seen_running = True
            saved_title = fresh_lifecycle_title(
                execution_status=execution_status,
                execution_order=saved_order if isinstance(saved_order, int) else current_order,
                seen_running=True,
            )
            if current_order is not None:
                should_save = True
        elif is_executed and current_order is not None:
            if seen_running:
                saved_order = current_order
                baseline_order = current_order
                saved_title = fresh_lifecycle_title(
                    execution_status="executed",
                    execution_order=current_order,
                    seen_running=True,
                )
                if current_order != prev.get("baseline_order"):
                    should_save = True
            elif baseline_order is not None and previous_title and TITLE_NOT_EXECUTED not in previous_title:
                saved_order = baseline_order
                saved_title = previous_title
            else:
                saved_order = None
                saved_title = TITLE_NOT_EXECUTED
        elif current_order is not None and seen_running:
            saved_order = current_order
            baseline_order = current_order
            saved_title = fresh_lifecycle_title(
                execution_status=execution_status,
                execution_order=current_order,
                seen_running=True,
            )
        elif baseline_order is not None and previous_title:
            saved_order = baseline_order
            saved_title = previous_title
        else:
            saved_order = None
            saved_title = TITLE_NOT_EXECUTED
    else:
        saved_order = current_order
        saved_title = current_title

    revision_row = {
        "baseline_order": baseline_order,
        "seen_running": seen_running,
        "title": saved_title,
    }
    save_slice = {
        "type": "code",
        "index": cell.get("index"),
        "input": cell.get("input"),
        "output": cell.get("output"),
        "execution_order": saved_order,
        "execution_title": saved_title,
        "execution_status": execution_status,
    }
    cell_uuid = cell.get("uuid") or cell.get("data_uuid")
    if cell_uuid:
        save_slice["uuid"] = str(cell_uuid)
    return save_slice, revision_row, should_save


def board_row_for_mode(cell: dict[str, Any], *, kernel_scenario: str) -> dict[str, Any]:
    """Single cell row for tools / terminal monitor (mode-aware fields)."""
    scenario = normalize_scenario(kernel_scenario)
    try:
        idx = int(cell.get("index"))
    except (TypeError, ValueError):
        idx = None
    order = cell.get("execution_order")
    title = str(cell.get("execution_title") or "").strip()
    has_output = bool(str(cell.get("output") or "").strip())

    row: dict[str, Any] = {
        "cell_index": idx,
        "has_output": has_output,
    }

    if scenario_is_off(scenario):
        return row

    if scenario_is_on(scenario):
        row["execution_order"] = order
        row["execution_title"] = title or TITLE_NOT_EXECUTED
        row["ran_this_session"] = bool(
            order is not None
            and TITLE_NOT_EXECUTED not in title
            and TITLE_PENDING not in title
            and TITLE_RUNNING not in title
            and ("Cell executed" in title or title.startswith("Execution"))
        )
        return row

    row["execution_order"] = order
    row["execution_title"] = title
    return row


def build_execution_board(
    cells: list[dict[str, Any]],
    *,
    kernel_scenario: str,
) -> list[dict[str, Any]]:
    code_cells = [
        c for c in cells
        if str(c.get("type") or "code") == "code"
    ]
    return [
        board_row_for_mode(c, kernel_scenario=kernel_scenario)
        for c in sorted(code_cells, key=lambda x: int(x.get("index", 0)))
    ]


def build_kernel_execution_report(
    *,
    url: str,
    cells: list[dict[str, Any]],
    kernel_scenario: str,
    kernel_session_started_at: str | None = None,
    kernel_session_stopped_at: str | None = None,
) -> dict[str, Any]:
    scenario = normalize_scenario(kernel_scenario)
    label = SCENARIO_LABELS.get(scenario, "unknown")
    board = build_execution_board(cells, kernel_scenario=scenario)

    if scenario_is_off(scenario):
        guidance = "Kernel OFF: no live execution tracking."
    elif scenario_is_on(scenario):
        guidance = (
            "Kernel ON: cells with 'Cell executed …' ran this session. "
            "Execution is preserved across page reload while kernel stays on."
        )
    else:
        guidance = "Kernel state unknown — refresh notebook scrape."

    pending = [r for r in board if TITLE_PENDING in str(r.get("execution_title") or "")]
    running = [r for r in board if TITLE_RUNNING in str(r.get("execution_title") or "")]

    return {
        "ok": True,
        "url": url,
        "kernel_scenario": scenario,
        "kernel_scenario_label": label,
        "kernel_session_started_at": kernel_session_started_at,
        "kernel_session_stopped_at": kernel_session_stopped_at,
        "guidance": guidance,
        "cell_count": len(board),
        "cells": board,
        "summary": (
            f"Kernel {label}: {len(board)} code cells, "
            f"{len([r for r in board if r.get('execution_order') is not None])} with order, "
            f"{len(pending)} pending, {len(running)} running"
        ),
        "pending_cells": [r["cell_index"] for r in pending if r.get("cell_index") is not None],
        "running_cells": [r["cell_index"] for r in running if r.get("cell_index") is not None],
    }
