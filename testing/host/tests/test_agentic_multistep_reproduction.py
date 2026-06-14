"""
Reproduction tests for multi-step agentic workflows (Tests A–G).

Each test documents Expected Behavior, Actual Behavior (host-side), and Failure Point
when the assertion fails. These exercise batch executor / parser / context budget —
not live LLM or browser dispatch.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_batch_executor import (
    ParsedToolCall,
    execute_run_queue_sequential,
    normalize_batch_indices,
    normalize_sequential_insert_anchors,
    partition_batch,
    workflow_needs_llm_followup,
)
from testing.host.agentic_text_tools import parse_text_tool_batch, strip_tool_batch_from_text
from testing.host.context_budget import fit_react_messages_to_budget
from testing.host.agentic_verification import count_verification_messages


# --- Test A: edit cell 10 only ---


def test_a_edit_cell_10_single_write():
    """Expected: one edit dispatches, no run queue, await summary gate set."""
    calls = [ParsedToolCall("1", "edit_cell_by_index", {"cell_index": 10, "content": "x=1"})]
    execute, deferred = partition_batch(calls)
    assert deferred == []
    assert len(execute) == 1
    assert execute[0].name == "edit_cell_by_index"
    # After successful queue-complete batch, ReAct loop stops; tool_final handles summary.
    assert workflow_needs_llm_followup({
        "verified": True,
        "batch_executed": True,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
    }) is False


# --- Test B: edit cell 10 + run cell 10 ---


def test_b_edit_then_run_same_index():
    """Expected: edit and run both in execute_now (run after edit, nothing deferred)."""
    calls = [
        ParsedToolCall("1", "edit_cell_by_index", {"cell_index": 10, "content": "print(1)"}),
        ParsedToolCall("2", "run_cell", {"cell_index": 10}),
    ]
    execute, deferred = partition_batch(calls)
    assert deferred == []
    names = [c.name for c in execute]
    assert names == ["edit_cell_by_index", "run_cell"]
    assert all(c.args["cell_index"] == 10 for c in execute if "cell_index" in c.args)


# --- Test C: insert below 10, edit inserted, run inserted ---


def test_c_insert_edit_run_index_remap():
    """Expected: edit/run target new cell 11 when inserting below anchor 10."""
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 10, "direction": "below"}),
        ParsedToolCall("2", "edit_cell_by_index", {"cell_index": 10, "content": "print(1)"}),
        ParsedToolCall("3", "run_cell", {"cell_index": 10}),
    ]
    out = normalize_batch_indices(calls)
    assert out[1].args["cell_index"] == 11
    assert out[2].args["cell_index"] == 11


# --- Test D: insert 5 consecutive cells below same anchor ---


def test_d_five_consecutive_inserts_anchor_chain():
    """Expected: anchors 10,11,12,13,14 — new cells at 11,12,13,14,15."""
    calls = [
        ParsedToolCall(f"i{i}", "insert_cell", {"index": 10, "direction": "below"})
        for i in range(5)
    ]
    out = normalize_sequential_insert_anchors(calls)
    anchors = [c.args["index"] for c in out]
    assert anchors == [10, 11, 12, 13, 14]
    # normalize_batch_indices only remaps edit/run for SINGLE insert batches.
    multi = normalize_batch_indices(out)
    assert len([c for c in multi if c.name == "insert_cell"]) == 5


def test_d_multi_insert_with_edit_run_remaps():
    """Multi-insert edit/run indices remapped to 11,12 when LLM uses stale anchor."""
    calls = normalize_sequential_insert_anchors([
        ParsedToolCall("1", "insert_cell", {"index": 10, "direction": "below"}),
        ParsedToolCall("2", "insert_cell", {"index": 10, "direction": "below"}),
        ParsedToolCall("3", "edit_cell_by_index", {"cell_index": 11, "content": "a"}),
        ParsedToolCall("4", "edit_cell_by_index", {"cell_index": 12, "content": "b"}),
    ])
    out = normalize_batch_indices(calls)
    edits = [c.args["cell_index"] for c in out if c.name == "edit_cell_by_index"]
    assert edits == [11, 12]


# --- Test E: error in middle of run queue ---


@patch("testing.host.agentic_batch_executor.wait_for_cell_run")
@patch("testing.host.agentic_batch_executor._dispatch_run_cell")
@patch("testing.host.agentic_batch_executor.load_notebook_snapshot")
@patch("testing.host.agentic_batch_executor.time.sleep")
def test_e_run_queue_stops_on_middle_error(mock_sleep, mock_snap, mock_dispatch, mock_wait):
    """Expected: cells 10,11 run; 12 pending; workflow_needs_llm_followup True."""
    mock_snap.return_value = ({}, "live")
    mock_dispatch.return_value = {"ok": True}
    mock_wait.side_effect = [
        {"ok": True, "output": "ok\n", "run_succeeded": True},
        {"ok": True, "output": "NameError: x\n", "run_succeeded": False, "has_error": True},
    ]
    completed, waits, pending = execute_run_queue_sequential(
        [10, 11, 12],
        executed=[],
        registry=MagicMock(),
        url="https://x/edit",
        tab_id=None,
        mode="agentic",
        browser_tool_allowed=lambda _m, _t: (True, None),
        inter_delay=0.0,
    )
    assert completed == [10, 11]
    assert pending == [12]
    verification = {
        "verified": False,
        "needs_fix": True,
        "run_queue_stopped": True,
        "pending_run_cells": pending,
        "batch_executed": True,
    }
    assert workflow_needs_llm_followup(verification) is True


# --- Test F: error on last cell ---


@patch("testing.host.agentic_batch_executor.wait_for_cell_run")
@patch("testing.host.agentic_batch_executor._dispatch_run_cell")
@patch("testing.host.agentic_batch_executor.load_notebook_snapshot")
@patch("testing.host.agentic_batch_executor.time.sleep")
def test_f_run_queue_error_on_last_cell(mock_sleep, mock_snap, mock_dispatch, mock_wait):
    """Expected: all prior cells complete; last fails; no pending runs."""
    mock_snap.return_value = ({}, "live")
    mock_dispatch.return_value = {"ok": True}
    mock_wait.side_effect = [
        {"ok": True, "output": "1\n", "run_succeeded": True},
        {"ok": True, "output": "2\n", "run_succeeded": True},
        {"ok": True, "output": "SyntaxError\n", "run_succeeded": False, "has_error": True},
    ]
    completed, waits, pending = execute_run_queue_sequential(
        [10, 11, 12],
        executed=[],
        registry=MagicMock(),
        url="https://x/edit",
        tab_id=None,
        mode="agentic",
        browser_tool_allowed=lambda _m, _t: (True, None),
        inter_delay=0.0,
    )
    # Host appends failed cell to completed before stop_on_error returns.
    assert completed == [10, 11, 12]
    assert pending == []
    assert workflow_needs_llm_followup({
        "verified": False,
        "needs_fix": True,
        "tool_queue_status": "error",
        "batch_executed": True,
    }) is True


# --- Test G: pipeline requiring 3 LLM rounds (host logic) ---


def test_g_three_round_message_budget_preserves_react_state():
    """
    ReAct-protected trim keeps original task, tool batch, and verification under pressure.
    """
    original = "ORIGINAL TASK: edit cell 10 and run it"
    verification = {"verified": True, "batch_executed": True, "cell_index": 10}
    batch_payload = json.dumps(verification) * 400

    messages = [
        {"role": "system", "content": "system " * 50},
        {"role": "user", "content": original, "_react_original_user": True},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}], "_react_tool_batch": True},
        {"role": "user", "content": f"__react_batch_verification__\n{batch_payload}", "_react_verification": True},
        {"role": "assistant", "content": "old chat filler " * 500},
        {"role": "user", "content": "old chat filler " * 500},
    ]
    fitted, removed = fit_react_messages_to_budget(messages, max_tokens=400, original_user_prompt=original)
    assert any(original in str(m.get("content") or "") for m in fitted)
    assert any(m.get("_react_tool_batch") for m in fitted)
    assert count_verification_messages(fitted) >= 1
    assert removed


def test_g_workflow_skips_react_when_queue_complete():
    """Round 1 success with complete queue goes to tool_final only (no ReAct round 2)."""
    assert workflow_needs_llm_followup({
        "verified": True,
        "batch_executed": True,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "await_llm_summary": True,
    }) is False


# --- Parser edge cases (Phase 5 evidence) ---


def test_parser_prose_before_batch_still_parses():
    text = 'Sure!\n<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":1}}]</agent_tool_batch>'
    assert len(parse_text_tool_batch(text)) == 1


def test_parser_prose_after_batch_ignored_for_tools():
    text = '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":1}}]</agent_tool_batch>\nDone!'
    assert len(parse_text_tool_batch(text)) == 1
    assert "Done" in strip_tool_batch_from_text(text) or strip_tool_batch_from_text(text) == ""


def test_parser_malformed_json_returns_empty():
    text = "<agent_tool_batch>{not json}</agent_tool_batch>"
    assert parse_text_tool_batch(text) == []


def test_parser_multiple_batches_merged():
    text = (
        '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":1}}]</agent_tool_batch>'
        '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":2}}]</agent_tool_batch>'
    )
    from testing.host.agentic_text_tools import parse_text_tool_batch_result
    result = parse_text_tool_batch_result(text)
    assert result.multiple_batches is True
    assert len(result.tool_calls) == 2
