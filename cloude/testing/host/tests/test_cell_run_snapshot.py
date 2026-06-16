"""Acceptance tests for cell run snapshot detection and ReAct continuation."""

from __future__ import annotations

import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_batch_executor import (
    ParsedToolCall,
    _run_wait_failed,
    _sort_tool_calls,
    workflow_needs_llm_followup,
)
from testing.host.cell_run_snapshot import (
    cell_execution_snapshot,
    detect_run_verification,
    format_cell_run_evidence,
)
from testing.host.execution_integrity import (
    ExecutionIntegrityState,
    apply_final_integrity_gate,
)
from testing.host.strict_execution_engine import (
    RunCellResult,
    attach_strict_execution,
    build_run_cell_result,
)


def _base_verification(**kw) -> dict:
    return {
        "verified": kw.get("verified", True),
        "batch_executed": True,
        "tool_queue_complete": True,
        "tool_queue_status": "complete",
        "run_queue_complete": True,
        **{k: v for k, v in kw.items() if k != "verified"},
    }


# A — Run cell 31: detect actual execution via order increase
def test_a_run_cell_detects_execution_order_increase(enable_execution_metadata):
    before = {"index": 31, "input": "df['price']", "output": "", "execution_order": 11}
    after = {
        "index": 31,
        "input": "df['price']",
        "output": "",
        "execution_order": 12,
        "execution_title": "Execution #12",
    }
    det = detect_run_verification(before, after)
    assert det["run_verified"] is True
    assert det["execution_order_increased"] is True

    wait = {
        "ok": True,
        "run_verified": True,
        "run_succeeded": True,
        "cell_index": 31,
        "execution_order": 12,
        "output": "",
        "run_snapshot": {"before": det["before"], "after": det["after"]},
    }
    result = build_run_cell_result(31, wait)
    assert result.run_verified is True
    assert result.success is True
    assert result.pending is False


# B — KeyError: traceback to LLM, no success claim
def test_b_keyerror_traceback_blocks_success():
    before = {"index": 31, "input": "x", "output": "", "execution_order": 5}
    after = {
        "index": 31,
        "input": "x",
        "output": "KeyError: 'price'\n",
        "execution_order": 6,
    }
    det = detect_run_verification(before, after)
    assert det["run_verified"] is True

    from testing.host.agentic_batch_executor import analyze_cell_output

    analysis = analyze_cell_output(after["output"])
    wait = {
        "ok": True,
        "run_verified": True,
        "run_succeeded": False,
        "cell_index": 31,
        "output": after["output"],
        **analysis,
    }
    result = build_run_cell_result(31, wait)
    assert result.success is False
    assert "KeyError" in str(result.traceback)

    from testing.host.strict_execution_engine import ExecutionQueueState

    state = ExecutionQueueState(edited_cells={31}, run_results=[result])
    v = attach_strict_execution(
        _base_verification(),
        queue_state=state,
        user_prompt="Fix error in cell 31",
        executor_called=True,
    )
    assert v["strict_goal_verified"] is False
    assert v["continue_react_loop"] is True
    assert workflow_needs_llm_followup(v) is True

    final, blocked = apply_final_integrity_gate(
        "The notebook now runs successfully.",
        ExecutionIntegrityState(),
        verification=v,
        action_required=True,
    )
    assert blocked is True


# C — Fix cell 31: edit+run+verify continues ReAct until proof
def test_c_fix_request_forces_react_continuation_after_run():
    from testing.host.strict_execution_engine import ExecutionQueueState

    state = ExecutionQueueState(edited_cells={31})
    state.run_results.append(
        RunCellResult(
            cell_index=31,
            run_verified=True,
            finished=True,
            success=True,
            output="ok\n",
            execution_order=12,
        )
    )
    v = attach_strict_execution(
        _base_verification(),
        queue_state=state,
        user_prompt="Fix error in cell 31 and verify",
        executor_called=True,
    )
    assert v["strict_goal_verified"] is True
    assert v["continue_react_loop"] is True
    assert workflow_needs_llm_followup(v) is True
    assert "EXECUTION REPORT" in v.get("execution_report_text", "")


# D — Five cells: writes before runs
def test_d_five_cell_batch_write_then_run_order():
    calls = []
    for i in range(5):
        calls.append(ParsedToolCall(f"i{i}", "insert_cell", {"index": 10 + i}))
    for i in range(5):
        calls.append(ParsedToolCall(f"e{i}", "edit_cell_by_index", {"cell_index": 11 + i, "content": f"x={i}"}))
    for i in range(5):
        calls.append(ParsedToolCall(f"r{i}", "run_cell", {"cell_index": 11 + i}))
    sorted_calls = _sort_tool_calls(calls)
    names = [c.name for c in sorted_calls]
    first_run = names.index("run_cell")
    assert names[:first_run].count("insert_cell") == 5
    assert names[:first_run].count("edit_cell_by_index") == 5
    assert names[first_run:].count("run_cell") == 5


# E — No-output cell verifies via execution order
def test_e_silent_cell_run_verified_without_output(enable_execution_metadata):
    before = {"index": 10, "input": "a = 1", "output": "", "execution_order": 3}
    after = {"index": 10, "input": "a = 1", "output": "", "execution_order": 4, "execution_title": "Execution #4"}
    det = detect_run_verification(before, after)
    assert det["run_verified"] is True
    wait = {
        "ok": True,
        "run_verified": True,
        "cell_index": 10,
        "output": "",
        "execution_order": 4,
        "run_snapshot": {"before": det["before"], "after": det["after"]},
    }
    result = build_run_cell_result(10, wait)
    assert result.run_verified is True
    assert result.success is True
    assert _run_wait_failed({**wait, "run_cell_result": result.to_dict()}) is False


# F — Output cell captured
def test_f_output_cell_captured_in_evidence():
    entry = {
        "cell_index": 5,
        "run_verified": True,
        "success": True,
        "output": "shape=(70751,20)\n",
        "execution_order": 8,
    }
    text = format_cell_run_evidence(entry)
    assert "Run verified: YES" in text
    assert "Success: YES" in text
    assert "shape=(70751,20)" in text


# G — Error cell traceback captured
def test_g_error_cell_traceback_in_evidence():
    entry = {
        "cell_index": 31,
        "run_verified": True,
        "success": False,
        "traceback": "KeyError: price",
        "source": "df['price']",
    }
    text = format_cell_run_evidence(entry)
    assert "Success: NO" in text
    assert "KeyError: price" in text


def test_dispatch_ok_without_snapshot_change_not_verified():
    before = {"index": 31, "input": "x", "output": "old", "execution_order": 10}
    after = dict(before)
    det = detect_run_verification(before, after)
    assert det["run_verified"] is False

    wait = {"ok": True, "cell_index": 31, "output": "old", "run_verified": False}
    result = build_run_cell_result(31, wait)
    assert result.run_verified is False
    assert result.pending is True
    assert _run_wait_failed(wait) is True


def test_cell_execution_snapshot_hashes(enable_execution_metadata):
    snap = cell_execution_snapshot({"input": "print(1)", "output": "1\n", "execution_order": 2})
    assert snap["source_hash"]
    assert snap["output_hash"]
    assert snap["execution_order"] == 2
