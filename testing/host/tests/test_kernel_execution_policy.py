"""Tests for kernel execution policy."""

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.kernel_execution_policy import (
    SCENARIO_OFF,
    SCENARIO_ON,
    TITLE_NOT_EXECUTED,
    TITLE_PENDING,
    TITLE_RUNNING,
    build_execution_board,
    fresh_lifecycle_title,
    resolve_cell_execution,
    scenario_is_off,
)


def test_off_mode_no_execution_info():
    cell = {
        "index": 5,
        "input": "x=1",
        "output": "1\n",
        "execution_order": 3,
        "execution_title": "Execution #3 from DOM",
        "execution_status": "executed",
    }
    save, rev, _ = resolve_cell_execution(
        cell,
        kernel_scenario=SCENARIO_OFF,
        previous_revision={},
        scenario_entered=False,
    )
    assert save["execution_order"] is None
    assert save["execution_title"] == ""
    assert scenario_is_off(SCENARIO_OFF)


def test_on_mode_clears_on_scenario_enter():
    cell = {
        "index": 2,
        "input": "import pandas",
        "output": "old\n",
        "execution_order": 5,
        "execution_title": "Execution #5",
        "execution_status": "executed",
    }
    save, _, _ = resolve_cell_execution(
        cell,
        kernel_scenario=SCENARIO_ON,
        previous_revision={"baseline_order": 5, "seen_running": True},
        scenario_entered=True,
    )
    assert save["execution_order"] is None
    assert save["execution_title"] == TITLE_NOT_EXECUTED


def test_fresh_lifecycle_titles():
    assert fresh_lifecycle_title(execution_status="queued", execution_order=None, seen_running=False) == TITLE_PENDING
    assert fresh_lifecycle_title(execution_status="running", execution_order=2, seen_running=True) == TITLE_RUNNING
    assert "Cell executed" in fresh_lifecycle_title(
        execution_status="executed", execution_order=4, seen_running=True
    )


def test_on_running_then_executed():
    cell = {
        "index": 10,
        "input": "print(1)",
        "output": "",
        "execution_order": 1,
        "execution_title": "",
        "execution_status": "running",
    }
    save, rev, changed = resolve_cell_execution(
        cell,
        kernel_scenario=SCENARIO_ON,
        previous_revision={},
        scenario_entered=False,
    )
    assert save["execution_title"] == TITLE_RUNNING
    assert rev["seen_running"] is True
    assert changed is True

    cell2 = dict(cell)
    cell2["execution_status"] = "executed"
    cell2["output"] = "1\n"
    save2, _, changed2 = resolve_cell_execution(
        cell2,
        kernel_scenario=SCENARIO_ON,
        previous_revision=rev,
        scenario_entered=False,
    )
    assert save2["execution_order"] == 1
    assert "Cell executed" in save2["execution_title"]
    assert changed2 is True


def test_on_preserves_revision_on_reload():
    cell = {
        "index": 3,
        "input": "df=1",
        "output": "ok",
        "execution_order": 7,
        "execution_title": "Cell executed (Execution #7)",
        "execution_status": "executed",
    }
    save, _, _ = resolve_cell_execution(
        cell,
        kernel_scenario=SCENARIO_ON,
        previous_revision={"baseline_order": 7, "seen_running": True, "title": "Cell executed (Execution #7)"},
        scenario_entered=False,
    )
    assert save["execution_order"] == 7
    assert "Cell executed" in save["execution_title"]


def test_board_off_vs_on():
    cells = [
        {"type": "code", "index": 1, "execution_order": 2, "execution_title": "", "execution_status": "executed"},
    ]
    off_board = build_execution_board(cells, kernel_scenario=SCENARIO_OFF)
    assert "execution_order" not in off_board[0]

    cells[0]["execution_title"] = TITLE_NOT_EXECUTED
    on_board = build_execution_board(cells, kernel_scenario=SCENARIO_ON)
    assert on_board[0].get("execution_title") == TITLE_NOT_EXECUTED
