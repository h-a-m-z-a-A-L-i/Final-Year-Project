"""Regression tests for execution integrity gate."""

from __future__ import annotations

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.execution_integrity import (
    ExecutionIntegrityState,
    apply_final_integrity_gate,
    block_success_language_legacy_only,
    compute_host_goal_verified,
    record_parsed_tools,
    update_integrity_from_verification,
)
from testing.host.agent_goal_verification import apply_goal_verification_layer


CELL30_SUCCESS_CLAIM = (
    "The error in cell 30 has been fixed and the notebook runs successfully without errors."
)


def _fix_error_verification_keyerror(cell_index: int = 30) -> dict:
    out = "KeyError: 'price'"
    base = {
        "verified": True,
        "needs_fix": False,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "batch_executed": True,
        "queue_cell_evidence": {
            "cells": [{"cell_index": cell_index, "input": "df['price']", "output": out}],
        },
        "executed": [
            {"tool": "edit_cell_by_index", "cell_index": cell_index, "dispatched": True},
            {"tool": "run_cell", "cell_index": cell_index, "dispatched": True},
        ],
        "tool_queue": {
            "run_completed": [cell_index],
            "run_requested": [cell_index],
            "run_pending": [],
        },
    }
    return apply_goal_verification_layer(
        base,
        user_prompt=f"fix the error in cell {cell_index} and test until it runs successfully without errors",
        expected_edits={cell_index: "df['price']"},
        run_waits=[{"ok": True, "output": out, "run_succeeded": False}],
        run_indices=[cell_index],
    )


def test_a_cell30_historical_success_blocked():
    """Cell 30 historical: false verified batch + success claim → blocked."""
    verification = _fix_error_verification_keyerror(30)
    assert verification["goal_verified"] is False

    state = ExecutionIntegrityState()
    update_integrity_from_verification(
        state,
        parsed_tool_count=2,
        verification=verification,
        executor_called=True,
    )
    assert state.goal_verified is False

    final, blocked = apply_final_integrity_gate(
        CELL30_SUCCESS_CLAIM,
        state,
        verification=verification,
        action_required=True,
        goal="fix cell 30",
    )
    assert blocked is True
    assert "Execution could not be verified" in final
    assert "successfully" not in final.lower()


def test_b_parsed_tools_no_executor_blocked():
    state = record_parsed_tools(ExecutionIntegrityState(), 2)
    assert state.parsed_tool_count == 2
    assert state.executor_called is False

    ok, reason = compute_host_goal_verified(state, None, action_required=True)
    assert ok is False
    assert reason == "executor_never_ran"

    final, blocked = apply_final_integrity_gate(
        "Task completed successfully.",
        state,
        verification=None,
        action_required=True,
    )
    assert blocked is True
    assert "Tool execution did not complete" in final


def test_c_verification_missing_blocked():
    state = ExecutionIntegrityState()
    state.parsed_tool_count = 1
    state.executor_called = True
    state.bot_commands_dispatched = 0

    ok, reason = compute_host_goal_verified(state, None, action_required=True)
    assert ok is False
    assert reason in ("verification_missing", "parsed_tools_no_bot_commands")

    final, blocked = apply_final_integrity_gate(
        "Verified and fixed successfully.",
        state,
        verification=None,
        action_required=True,
    )
    assert blocked is True


def test_d_successful_edit_and_run_allowed():
    verification = {
        "verified": True,
        "goal_verified": True,
        "batch_executed": True,
        "executed": [
            {"tool": "edit_cell_by_index", "cell_index": 5, "dispatched": True},
            {"tool": "run_cell", "cell_index": 5, "dispatched": True},
        ],
        "tool_verifications": [
            {
                "tool": "edit_cell_by_index",
                "verification_status": "verified",
                "evidence": {"cell_index": 5, "source_matches_expected": True},
            },
            {
                "tool": "run_cell",
                "verification_status": "verified",
                "evidence": {"cell_index": 5, "execution_state": "completed"},
            },
        ],
    }
    state = update_integrity_from_verification(
        ExecutionIntegrityState(),
        parsed_tool_count=2,
        verification=verification,
        executor_called=True,
    )
    assert state.goal_verified is True

    claim = "Cell 5 was fixed and runs successfully without errors."
    final, blocked = apply_final_integrity_gate(
        claim,
        state,
        verification=verification,
        action_required=True,
    )
    assert blocked is False
    assert final == claim


def test_legacy_gate_misses_missing_verification():
    """Before integrity gate: no verification dict → success passed through."""
    text = "Fixed successfully."
    assert block_success_language_legacy_only(text, None) == text
