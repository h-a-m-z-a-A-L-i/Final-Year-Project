"""Tests for goal-aware verification layer."""

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agent_goal_verification import (
    apply_goal_verification_layer,
    sanitize_false_success_language,
    verify_edit_cell,
    verify_run_cell,
    verify_user_goal,
)
from testing.host.agentic_batch_executor import finalize_tool_queue_verification


def test_edit_verification_success():
    tv = verify_edit_cell(3, "print('hi')", "print('hi')\n")
    assert tv["verification_status"] == "verified"
    assert tv["evidence"]["source_matches_expected"] is True


def test_edit_verification_mismatch():
    tv = verify_edit_cell(3, "print('fixed')", "print('broken')")
    assert tv["verification_status"] == "failed"
    assert tv["next_action_required"] is True


def test_run_verification_success():
    tv = verify_run_cell(
        29,
        run_wait={"ok": True, "run_verified": True, "output": "hello\n", "run_succeeded": True},
        cell_output="hello\n",
    )
    assert tv["verification_status"] == "verified"
    assert tv["evidence"]["execution_state"] == "completed"


def test_run_verification_exception():
    out = "Traceback (most recent call last):\nKeyError: 'price'"
    tv = verify_run_cell(
        29,
        run_wait={"ok": True, "run_verified": True, "output": out, "run_succeeded": False, "has_error": True},
        cell_output=out,
    )
    assert tv["verification_status"] == "failed"
    assert tv["evidence"]["execution_state"] == "error"


def test_visualization_goal_verified():
    evidence = {"cells": [{"cell_index": 5, "output": "Figure(640x480)\n"}]}
    tool_v = [
        verify_run_cell(
            5,
            run_wait={"ok": True, "run_verified": True, "output": evidence["cells"][0]["output"], "run_succeeded": True},
            cell_output=evidence["cells"][0]["output"],
        )
    ]
    goal = verify_user_goal("Generate visualization", tool_verifications=tool_v, evidence=evidence, run_completed=[5])
    assert goal["goal_verified"] is True


def test_train_model_goal_failed_on_error():
    out = "ValueError: could not convert string to float"
    tool_v = [verify_run_cell(10, cell_output=out)]
    goal = verify_user_goal("Train model on dataset", tool_verifications=tool_v, run_completed=[10])
    assert goal["goal_verified"] is False


def test_submission_csv_goal():
    out = "Saved submission.csv to /kaggle/working/submission.csv"
    evidence = {"cells": [{"cell_index": 12, "output": out}]}
    tool_v = [verify_run_cell(12, cell_output=out)]
    goal = verify_user_goal("Create submission.csv", tool_verifications=tool_v, evidence=evidence, run_completed=[12])
    assert goal["goal_verified"] is True


def test_false_success_prevention_sanitize():
    verification = {"goal_verified": False, "goal_reason": "Cell 29 still raises KeyError: price"}
    text = "The error has been fixed and executed successfully."
    cleaned = sanitize_false_success_language(text, verification)
    assert "could not be verified" in cleaned or "Cell 29" in cleaned
    assert "successfully" not in cleaned.lower() or "removed" in cleaned.lower()


def test_fix_error_cell_29_regression():
    out = "KeyError: 'price'"
    verification = {
        "verified": True,
        "needs_fix": False,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "queue_cell_evidence": {
            "cells": [{"cell_index": 29, "input": "df['price']", "output": out}],
        },
        "executed": [{"tool": "run_cell", "cell_index": 29, "dispatched": True}],
        "tool_queue": {"run_completed": [29], "run_requested": [29], "run_pending": []},
    }
    enriched = apply_goal_verification_layer(
        verification,
        user_prompt="Fix error in cell 29",
        expected_edits={},
        run_waits=[{"ok": True, "output": out, "run_succeeded": False}],
        run_indices=[29],
    )
    assert enriched["goal_verified"] is False
    assert enriched["verified"] is False
    assert enriched["needs_fix"] is True
    assert enriched.get("tool_queue_status") in ("error", "verification_failed")
    assert 29 in (enriched.get("batch_audit") or {}).get("failed_cells", [])


def test_finalize_does_not_claim_success_on_failed_run():
    base = {
        "verified": False,
        "needs_fix": True,
        "execution_error": {"cell_index": 29, "error_type": "KeyError", "error_summary": "KeyError: price"},
        "batch": [{"tool": "run_cell", "ok": False, "cell_index": 29}],
    }
    out = finalize_tool_queue_verification(
        dict(base),
        registry=None,
        url="https://test/edit",
        expected_edits={},
        run_requested=[29],
        run_completed=[29],
        run_pending=[],
        user_prompt="Fix error in cell 29",
        run_waits=[{"ok": True, "output": "KeyError: price", "run_succeeded": False}],
    )
    assert out.get("goal_verified") is False
    assert out.get("await_llm_summary") is False


def test_hallucinated_success_regression():
    verification = {
        "goal_verified": False,
        "batch_audit": {"failed_cells": [29], "next_required_action": "Fix KeyError in cell 29"},
    }
    claim = "Verified output. Task completed successfully."
    result = sanitize_false_success_language(claim, verification)
    assert "could not be verified" in result or "Cell 29" in result
