"""Stress tests for strict execution queue engine (Phases 1–10)."""

from __future__ import annotations

import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_batch_executor import (
    ParsedToolCall,
    analyze_cell_output,
    _run_wait_failed,
)
from testing.host.execution_integrity import (
    ExecutionIntegrityState,
    apply_final_integrity_gate,
    compute_host_goal_verified,
    record_parsed_tools,
)
from testing.host.strict_execution_engine import (
    ExecutionQueueState,
    QueuedOperation,
    RunCellResult,
    attach_strict_execution,
    build_execution_queue,
    build_execution_report,
    build_run_cell_result,
    check_target_cell_enforcement,
    compute_strict_goal_verified,
    format_execution_report_message,
    record_queue_progress,
)


def _tool_batch(*specs):
    out = []
    for i, (name, args) in enumerate(specs):
        out.append(
            {
                "id": f"tc_{i}",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        )
    return out


def _base_verification(*, complete: bool = True, verified: bool = True) -> dict:
    return {
        "verified": verified,
        "batch_executed": True,
        "tool_queue_complete": complete,
        "tool_queue_status": "complete" if complete else "incomplete",
        "run_queue_complete": complete,
    }


# A — Fix failing cell 31: edit + run + verify
def test_a_fix_failing_cell_31_edit_run_verify():
    batch = _tool_batch(
        ("edit_cell_by_index", {"cell_index": 31, "content": "df['price']"}),
        ("run_cell", {"cell_index": 31}),
    )
    state = build_execution_queue(batch)
    assert len(state.operations) == 2
    assert [o.tool for o in state.operations] == ["edit_cell_by_index", "run_cell"]

    record_queue_progress(state, tool="edit_cell_by_index", dispatched=True, cell_index=31)
    state.run_results.append(
        RunCellResult(
            cell_index=31,
            started=True,
            finished=True,
            run_verified=True,
            success=True,
            output="ok\n",
        )
    )

    verification = attach_strict_execution(
        _base_verification(),
        queue_state=state,
        user_prompt="Fix error in cell 31",
        executor_called=True,
    )
    assert 31 in state.edited_cells
    assert verification["strict_goal_verified"] is True
    assert verification["goal_verified"] is True
    report = verification["execution_report"]
    assert "edit_cell_by_index cell 31" in report["executed"]
    assert report["results"]["cell_31"]["success"] is True


# B — Five new cells: single batch queue (insert x5, edit x5, run x5)
def test_b_five_cell_single_batch_queue():
    specs = []
    for i in range(5):
        specs.append(("insert_cell", {"index": 10 + i, "direction": "below"}))
    for i in range(5):
        specs.append(("edit_cell_by_index", {"cell_index": 11 + i, "content": f"print({i})"}))
    for i in range(5):
        specs.append(("run_cell", {"cell_index": 11 + i}))
    state = build_execution_queue(_tool_batch(*specs))
    assert len(state.operations) == 15
    tools = [o.tool for o in state.operations]
    assert tools.count("insert_cell") == 5
    assert tools.count("edit_cell_by_index") == 5
    assert tools.count("run_cell") == 5
    assert tools.index("run_cell") > tools.index("edit_cell_by_index")


# C — run_cell error → traceback in next round evidence
def test_c_run_cell_error_traceback_in_report():
    state = ExecutionQueueState(
        operations=[
            QueuedOperation(0, "edit_cell_by_index", {"cell_index": 31, "content": "x"}),
            QueuedOperation(1, "run_cell", {"cell_index": 31}),
        ],
        edited_cells={31},
    )
    state.run_results.append(
        RunCellResult(
            cell_index=31,
            started=True,
            finished=True,
            run_verified=True,
            success=False,
            output="KeyError: 'price'",
            traceback="KeyError: 'price'",
            error_type="KeyError",
        )
    )
    verification = attach_strict_execution(
        {**_base_verification(verified=False), "needs_fix": True, "execution_error": {"cell_index": 31}},
        queue_state=state,
        user_prompt="Fix error in cell 31",
        executor_called=True,
    )
    assert verification["strict_goal_verified"] is False
    assert verification["strict_goal_reason"] == "run_cell_31_failed"
    er = verification["execution_report"]["error_recovery"]
    assert er["TARGET_FAILED"] is True
    assert er["cell"] == 31
    assert "KeyError" in str(er["traceback"])
    assert "TARGET FAILED" in verification["user_response_gate"]


# D — run_cell never finishes → pending, no success
def test_d_run_cell_never_finishes_pending_no_success():
    wait = {"ok": True, "cell_index": 31, "output": "", "run_completed": False, "pending": True}
    result = build_run_cell_result(31, wait)
    assert result.pending is True
    assert result.finished is False
    assert result.success is False

    analysis = analyze_cell_output("")
    assert analysis["pending"] is True
    assert analysis["run_succeeded"] is False
    assert _run_wait_failed({"ok": True, "output": "", "pending": True, "run_completed": False}) is True

    state = ExecutionQueueState(edited_cells={31}, run_results=[result])
    ok, reason = compute_strict_goal_verified(
        state,
        user_prompt="Fix cell 31",
        verification=_base_verification(),
        queue_complete=True,
        executor_called=True,
    )
    assert ok is False
    assert "pending" in reason or "not_verified" in reason


# E — tool batch parsed but executor not called
def test_e_parsed_tools_executor_never_ran():
    batch = _tool_batch(("edit_cell_by_index", {"cell_index": 31, "content": "x"}))
    state = build_execution_queue(batch)
    ok, reason = compute_strict_goal_verified(
        state,
        user_prompt="Fix cell 31",
        verification=None,
        executor_called=False,
    )
    assert ok is False
    assert reason == "executor_never_ran"

    integrity = record_parsed_tools(ExecutionIntegrityState(), 1)
    host_ok, host_reason = compute_host_goal_verified(integrity, None, action_required=True)
    assert host_ok is False
    assert host_reason == "executor_never_ran"


# F — executor called but verification missing
def test_f_executor_called_verification_missing():
    state = ExecutionQueueState(
        operations=[QueuedOperation(0, "run_cell", {"cell_index": 31})],
    )
    ok, reason = compute_strict_goal_verified(
        state,
        user_prompt="Run cell 31",
        verification=None,
        executor_called=True,
    )
    assert ok is False
    assert reason == "verification_missing"


# G — workaround new cell instead of fixing target
def test_g_workaround_cell_target_enforcement_fails():
    state = ExecutionQueueState(
        operations=[
            QueuedOperation(0, "insert_cell", {"index": 31, "direction": "below"}),
            QueuedOperation(1, "edit_cell_by_index", {"cell_index": 32, "content": "workaround"}),
            QueuedOperation(2, "run_cell", {"cell_index": 32}),
        ],
        inserted_cells={32},
        edited_cells={32},
    )
    state.run_results.append(
        RunCellResult(cell_index=32, started=True, finished=True, run_verified=True, success=True, output="ok")
    )
    target_ok, target_reason = check_target_cell_enforcement(state, "Fix error in cell 31")
    assert target_ok is False
    assert "target_cell_31" in target_reason

    verification = attach_strict_execution(
        _base_verification(),
        queue_state=state,
        user_prompt="Fix error in cell 31",
        executor_called=True,
    )
    assert verification["strict_goal_verified"] is False
    assert "target_cell_31" in verification["strict_goal_reason"]


def test_execution_report_only_evidence_no_hallucination():
    state = ExecutionQueueState(
        operations=[QueuedOperation(0, "run_cell", {"cell_index": 5})],
        run_results=[
            RunCellResult(cell_index=5, started=True, finished=True, run_verified=True, success=True, output="5\n"),
        ],
    )
    report = build_execution_report(state)
    msg = format_execution_report_message(report)
    parsed = json.loads(msg)
    assert parsed["EXECUTION_REPORT"] is True
    assert parsed["results"]["cell_5"]["success"] is True


def test_integrity_blocks_success_when_strict_fails():
    verification = attach_strict_execution(
        {**_base_verification(), "verified": True, "goal_verified": True},
        queue_state=ExecutionQueueState(
            run_results=[
                RunCellResult(
                    cell_index=30,
                    started=True,
                    finished=True,
                    run_verified=True,
                    success=False,
                    traceback="KeyError: 'price'",
                )
            ],
            edited_cells={30},
        ),
        user_prompt="Fix error in cell 30",
        executor_called=True,
    )
    state = ExecutionIntegrityState()
    state.parsed_tool_count = 2
    state.executor_called = True
    state.verification_received = True

    final, blocked = apply_final_integrity_gate(
        "The error has been fixed successfully.",
        state,
        verification=verification,
        action_required=True,
    )
    assert blocked is True
    assert "Execution could not be verified" in final
