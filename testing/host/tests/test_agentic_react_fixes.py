"""Regression tests for agentic ReAct fixes (Phases 1–9)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agent_state import (
    REACT_ORIGINAL_USER,
    empty_agent_state,
    format_agent_state_block,
    update_agent_state_from_verification,
)
from testing.host.agentic_batch_executor import (
    ParsedToolCall,
    normalize_batch_indices,
    workflow_followup_reason,
    workflow_needs_llm_followup,
)
from testing.host.agentic_verification import (
    append_batch_verification_message,
    build_compact_batch_verification,
    count_verification_messages,
)
from testing.host.context_budget import (
    estimate_messages_tokens,
    fit_react_messages_to_budget,
    _react_protected_indices,
)
import testing.host.prompt_engineering as pe


def _complete_verification(**overrides):
    base = {
        "verified": True,
        "batch_executed": True,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "run_queue_complete": True,
        "await_llm_summary": True,
        "pending_run_cells": [],
        "deferred_tool_calls": [],
        "needs_fix": False,
        "runs_executed": [10],
    }
    base.update(overrides)
    return base


# --- Phase 1: skip unnecessary ReAct round 2 ---


def test_single_edit_run_skips_react_round2():
    v = _complete_verification()
    assert workflow_needs_llm_followup(v) is False
    assert workflow_followup_reason(v) == "queue_complete_no_pending_go_to_final_summary"


def test_workflow_continues_on_deferred_tools():
    v = _complete_verification(
        tool_queue_status="incomplete",
        tool_queue_complete=False,
        deferred_tool_calls=[{"tool": "insert_cell", "args": {"index": 5}}],
    )
    assert workflow_needs_llm_followup(v) is True


def test_workflow_continues_on_error():
    v = _complete_verification(
        tool_queue_status="error",
        tool_queue_complete=False,
        needs_fix=True,
        execution_error={"cell_index": 10, "error_summary": "NameError"},
    )
    assert workflow_needs_llm_followup(v) is True


# --- Phase 2: single verification message ---


def _sample_verification():
    return _complete_verification(
        queue_cell_evidence={"cells": [{"cell_index": i, "input": "x", "output": "y"} for i in range(10)]},
    )


def test_verification_deduplication_2_tools():
    msgs: list = []
    append_batch_verification_message(msgs, _sample_verification(), round_idx=0)
    assert count_verification_messages(msgs) == 1


def test_verification_deduplication_5_and_10_tools():
    v = _sample_verification()
    full_one = json.dumps(v)
    compact_one = json.dumps(build_compact_batch_verification(v))
    for n in (2, 5, 10):
        msgs: list = []
        append_batch_verification_message(msgs, v, round_idx=0)
        assert count_verification_messages(msgs) == 1
        old_style_tokens = estimate_messages_tokens(
            [{"role": "tool", "content": full_one} for _ in range(n)]
        )
        new_style_tokens = estimate_messages_tokens(msgs)
        assert new_style_tokens < old_style_tokens
    assert len(compact_one) <= len(full_one) * 2


def test_verification_token_reduction_report():
    v = _sample_verification()
    full_dup_3 = estimate_messages_tokens(
        [{"role": "tool", "content": json.dumps(v)} for _ in range(3)]
    )
    msgs: list = []
    append_batch_verification_message(msgs, v, round_idx=0)
    single = estimate_messages_tokens(msgs)
    assert single < full_dup_3
    ratio = 1.0 - (single / max(1, full_dup_3))
    assert ratio > 0.5


# --- Phase 3: protected ReAct trimming ---


def _react_round2_messages(original: str, *, pad: str = "x" * 8000) -> list[dict]:
    return [
        {"role": "system", "content": "system prompt " + pad[:1000]},
        {"role": "user", "content": original, REACT_ORIGINAL_USER: True},
        {"role": "assistant", "content": "Working", "_react_tool_batch": True, "tool_calls": [{"id": "t1"}]},
        {
            "role": "user",
            "content": "__react_batch_verification__\n" + json.dumps({"verified": True}),
            "_react_verification": True,
        },
        {"role": "assistant", "content": "old history reply " + pad},
        {"role": "user", "content": "old history question " + pad},
    ]


def test_context_survival_three_round_workflow():
    original = "Edit cell 10 and run it"
    msgs = _react_round2_messages(original)
    for _ in range(3):
        fitted, removed = fit_react_messages_to_budget(msgs, max_tokens=2500, original_user_prompt=original)
        assert any(original in str(m.get("content") or "") for m in fitted)
        assert any(m.get("_react_tool_batch") or m.get("tool_calls") for m in fitted)
        assert count_verification_messages(fitted) >= 1
        msgs = fitted + [
            {"role": "assistant", "content": "step done", "_react_tool_batch": True, "tool_calls": [{"id": "t2"}]},
            {
                "role": "user",
                "content": "__react_batch_verification__\n" + json.dumps({"verified": True}),
                "_react_verification": True,
            },
        ]


def test_protected_indices_include_core_messages():
    original = "TASK: fix cell 5"
    msgs = _react_round2_messages(original, pad="y" * 100)
    protected = _react_protected_indices(msgs, original_user_prompt=original)
    assert 0 in protected
    assert any(msgs[i].get(REACT_ORIGINAL_USER) for i in protected)


# --- Phase 4 & 5: agent state ---


def test_agent_state_survival_under_trim():
    state = update_agent_state_from_verification(
        empty_agent_state(goal="run cells 10-12"),
        {
            "executed": [{"tool": "run_cell", "cell_index": 10}],
            "pending_run_cells": [11, 12],
            "needs_fix": True,
            "execution_error": {"cell_index": 10, "error_summary": "NameError: x"},
            "user_response_gate": "fix cell 10",
        },
        goal="run cells 10-12",
    )
    block = format_agent_state_block(state)
    assert "run cells 10-12" in block
    assert "NameError" in block
    assert "11" in block or "PENDING" in block


def test_error_recovery_mid_queue_state():
    state = update_agent_state_from_verification(
        empty_agent_state(),
        {
            "needs_fix": True,
            "execution_error": {"cell_index": 11, "error_summary": "SyntaxError"},
            "pending_run_cells": [12],
            "tool_queue_status": "error",
        },
    )
    assert state["last_error"]["cell_index"] == 11
    assert state["pending_steps"]


# --- Phase 6: multi-insert ---


def test_multi_insert_edit_run_three_cells():
    calls = []
    for i in range(3):
        calls.append(ParsedToolCall(f"ins{i}", "insert_cell", {"index": 10, "direction": "below"}))
    for i in range(3):
        calls.append(
            ParsedToolCall(f"edit{i}", "edit_cell_by_index", {"cell_index": 10, "content": f"print({i})"})
        )
    for i in range(3):
        calls.append(ParsedToolCall(f"run{i}", "run_cell", {"cell_index": 10}))
    out = normalize_batch_indices(calls)
    edit_indices = sorted(c.args["cell_index"] for c in out if c.name == "edit_cell_by_index")
    run_indices = sorted(c.args["cell_index"] for c in out if c.name == "run_cell")
    assert edit_indices == [11, 12, 13]
    assert run_indices == [11, 12, 13]


def test_insert_edit_run():
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 10, "direction": "below"}),
        ParsedToolCall("2", "edit_cell_by_index", {"cell_index": 10, "content": "print(1)"}),
        ParsedToolCall("3", "run_cell", {"cell_index": 10}),
    ]
    out = normalize_batch_indices(calls)
    assert out[1].args["cell_index"] == 11
    assert out[2].args["cell_index"] == 11


def test_single_edit_run():
    execute, deferred = __import__(
        "testing.host.agentic_batch_executor", fromlist=["partition_batch"]
    ).partition_batch(
        [
            ParsedToolCall("1", "edit_cell_by_index", {"cell_index": 10, "content": "x=1"}),
            ParsedToolCall("2", "run_cell", {"cell_index": 10}),
        ]
    )
    assert not deferred
    assert workflow_needs_llm_followup(_complete_verification()) is False


# --- Phase 7: prompt compression ---


def test_system_prompt_smaller_without_examples_agentic_text():
    with_examples = pe.estimate_system_prompt_tokens("agentic", text_tool_calls=False)
    without = pe.estimate_system_prompt_tokens("agentic", text_tool_calls=True)
    assert without["tool_examples"] == 0
    assert without["system_total"] <= with_examples["system_total"]


# --- Phase 8: trace (smoke) ---


def test_agent_trace_writes_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TRACE", "1")
    import testing.host.agent_trace as trace

    trace.TRACE_PATH = tmp_path / "agent_trace.log"
    trace.trace_react_event(event="test", round_idx=1, continue_reason="unit_test")
    assert trace.TRACE_PATH.is_file()
    data = json.loads(trace.TRACE_PATH.read_text(encoding="utf-8").strip())
    assert data["event"] == "test"


# --- Phase 9 aliases ---


def test_three_round_workflow():
    test_context_survival_three_round_workflow()


def test_multi_insert():
    test_multi_insert_edit_run_three_cells()


def test_error_recovery_mid_queue():
    test_error_recovery_mid_queue_state()


def test_context_survival():
    test_context_survival_three_round_workflow()


def test_agent_state_survival():
    test_agent_state_survival_under_trim()


def test_verification_deduplication():
    test_verification_deduplication_2_tools()
