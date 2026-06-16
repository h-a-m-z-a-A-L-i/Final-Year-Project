"""Tests for host-managed workflow planner."""

import json
import os
import sys
from unittest.mock import patch

import pytest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agent_planner import (
    PLAN_MEMORY_PATH,
    apply_plan_from_llm_response,
    build_plan_request_nudge,
    build_step_execution_nudge,
    clear_agent_plan,
    load_agent_plan,
    needs_explicit_plan,
    parse_plan_from_text,
    persist_agent_plan,
    planner_enabled,
    planning_phase_active,
    update_plan_from_verification,
)
from testing.host.agent_state import (
    empty_agent_state,
    format_agent_state_block,
    update_agent_state_from_verification,
)


TITANIC_PROMPT = (
    "Build a Titanic ML pipeline: load data, EDA, feature engineering, "
    "train a model, and evaluate accuracy."
)

EDA_PROMPT = (
    "Perform EDA and visualization on the housing dataset: summary stats, "
    "missing values, correlation heatmap, then distribution plots."
)

CNN_PROMPT = (
    "Create a CNN training notebook: load images, build model, train, "
    "and evaluate on validation set."
)

FE_PROMPT = (
    "Multi-cell feature engineering workflow: encode categoricals, scale numerics, "
    "create interaction terms, then split train/test."
)


def test_needs_explicit_plan_multi_step():
    assert needs_explicit_plan(TITANIC_PROMPT) is True
    assert needs_explicit_plan(EDA_PROMPT) is True
    assert needs_explicit_plan(CNN_PROMPT) is True
    assert needs_explicit_plan(FE_PROMPT) is True
    assert needs_explicit_plan("edit cell 3") is False


def test_parse_plan_from_text():
    text = (
        "PLAN:\n"
        "1. Load Titanic dataset\n"
        "2. Run EDA\n"
        "3. Train model\n"
        "4. Evaluate accuracy\n"
    )
    steps = parse_plan_from_text(text)
    assert steps == [
        "Load Titanic dataset",
        "Run EDA",
        "Train model",
        "Evaluate accuracy",
    ]


def test_apply_plan_sets_host_state():
    state = empty_agent_state(goal=TITANIC_PROMPT)
    text = "PLAN:\n1. Load data\n2. Train model\n"
    state, steps = apply_plan_from_llm_response(state, text, goal=TITANIC_PROMPT)
    assert len(steps) == 2
    assert state["current_step"] == 0
    assert state["pending_steps"] == ["Load data", "Train model"]
    assert "Load data" in format_agent_state_block(state)


def test_step_completion_advances_plan():
    state, _ = apply_plan_from_llm_response(
        empty_agent_state(goal=TITANIC_PROMPT),
        "PLAN:\n1. Load\n2. EDA\n3. Train\n",
        goal=TITANIC_PROMPT,
    )
    v_ok = {
        "verified": True,
        "batch_executed": True,
        "tool_queue_complete": True,
    }
    state, event = update_plan_from_verification(state, v_ok)
    assert event == "step_completed"
    assert state["current_step"] == 1
    assert 0 in state["plan_completed_indices"]
    assert state["pending_steps"] == ["EDA", "Train"]


def test_step_error_keeps_remaining_plan():
    state, _ = apply_plan_from_llm_response(
        empty_agent_state(goal=CNN_PROMPT),
        "PLAN:\n1. Load images\n2. Train CNN\n3. Evaluate\n",
        goal=CNN_PROMPT,
    )
    v_err = {
        "needs_fix": True,
        "execution_error": {"cell_index": 5, "error_summary": "CUDA OOM"},
    }
    state, event = update_plan_from_verification(state, v_err)
    assert event == "step_failed"
    assert state["current_step"] == 0
    assert len(state["plan_completed_indices"]) == 0
    assert len(state["pending_steps"]) == 3
    assert state["last_error"]["plan_step_index"] == 0

    v_retry_ok = {"verified": True, "batch_executed": True}
    state, event = update_plan_from_verification(state, v_retry_ok)
    assert event == "step_retried"
    assert state["current_step"] == 1


def test_format_injects_current_step():
    state, _ = apply_plan_from_llm_response(
        empty_agent_state(goal=FE_PROMPT),
        "PLAN:\n1. Encode\n2. Scale\n",
        goal=FE_PROMPT,
    )
    block = format_agent_state_block(state)
    assert "CURRENT STEP:" in block
    assert "WORKFLOW PLAN:" in block
    assert "[ ] 1. Encode <- CURRENT" in block
    nudge = build_step_execution_nudge(state)
    assert "ONLY the current plan step" in nudge
    assert "Encode" in nudge


def test_plan_persist_and_load(tmp_path):
    mem_file = tmp_path / "agent_plan_memory.json"
    key = "https://example.com/notebook/edit"
    state, _ = apply_plan_from_llm_response(
        empty_agent_state(goal=TITANIC_PROMPT),
        "PLAN:\n1. A\n2. B\n",
        goal=TITANIC_PROMPT,
    )
    with patch("testing.host.agent_planner.PLAN_MEMORY_PATH", mem_file):
        persist_agent_plan(key, state)
        loaded = load_agent_plan(key, goal=TITANIC_PROMPT)
        clear_agent_plan(key)
    assert loaded is not None
    assert loaded["plan"] == ["A", "B"]
    with patch("testing.host.agent_planner.PLAN_MEMORY_PATH", mem_file):
        assert load_agent_plan(key) is None


def test_planning_phase_active():
    state = empty_agent_state(goal=TITANIC_PROMPT)
    with patch.dict(os.environ, {"AGENTIC_PLANNER": "1"}):
        assert planning_phase_active(state, prompt=TITANIC_PROMPT) is True
        state, _ = apply_plan_from_llm_response(state, "PLAN:\n1. X\n", goal=TITANIC_PROMPT)
        assert planning_phase_active(state, prompt=TITANIC_PROMPT) is False


def test_without_plan_tool_state_unchanged():
    state = empty_agent_state(goal="run cell 1")
    v = {
        "executed": [{"tool": "run_cell", "cell_index": 1}],
        "verified": True,
        "tool_queue_complete": True,
        "pending_run_cells": [],
    }
    out = update_agent_state_from_verification(state, v, goal="run cell 1")
    assert "run_cell:1" in out["completed_steps"]


def test_planner_disabled():
    with patch.dict(os.environ, {"AGENTIC_PLANNER": "0"}):
        assert planner_enabled() is False


def test_build_plan_request_nudge():
    nudge = build_plan_request_nudge(TITANIC_PROMPT)
    assert "PLAN:" in nudge
    assert "Do NOT emit" in nudge


class _PlannerSimClient:
    """Simulates plan round then tool round."""

    def __init__(self, plan_text: str, tool_text: str):
        self.plan_text = plan_text
        self.tool_text = tool_text
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        content = self.plan_text if self.calls == 1 else self.tool_text
        msg = type("M", (), {"content": content, "tool_calls": []})()
        choice = type("C", (), {"message": msg})()
        resp = type("R", (), {"choices": [choice]})()
        resp.model_dump = lambda: {"choices": [{"message": {"content": content, "tool_calls": []}}]}
        return resp


def test_streaming_planner_plan_then_tools():
    os.environ["AGENTIC_TEXT_TOOLS"] = "1"
    os.environ["AGENTIC_PLANNER"] = "1"
    from testing.host.agentic_mode import set_dashboard_agentic_enabled
    import testing.host.streaming as streaming

    set_dashboard_agentic_enabled(True)
    plan = "PLAN:\n1. Edit cell 1\n2. Run cell 1\n"
    tools = (
        '<agent_tool_batch>[{"tool":"edit_cell_by_index","args":{"cell_index":1,"content":"x"}}]'
        "</agent_tool_batch>"
    )
    client = type("C", (), {"chat": type("Ch", (), {"completions": _PlannerSimClient(plan, tools)})})()
    streaming._LLM_CLIENT = client

    with patch("testing.host.streaming.send_msg", lambda *a, **k: None):
        with patch("testing.host.streaming.memory_store.append", lambda *a, **k: None):
            with patch("testing.host.streaming._wait_for_request_slot", lambda *a, **k: True):
                with patch("testing.host.streaming._check_token_limits", lambda: (True, {})):
                    with patch("testing.host.streaming._record_llm_usage", lambda *a, **k: None):
                        with patch("testing.host.streaming._finalize_request_attempt", lambda *a, **k: None):
                            with patch("testing.host.streaming._record_request_attempt", lambda *a, **k: None):
                                with patch("testing.host.agentic_tool_chain.build_direct_edit_from_prompt", lambda *a, **k: None):
                                    with patch("testing.host.notebook_query.prefetch_notebook_queries", lambda **k: ("", [])):
                                        with patch(
                                            "testing.host.agentic_batch_executor.execute_agentic_batch",
                                            lambda *a, **k: {
                                                "verified": True,
                                                "batch_executed": True,
                                                "tool_queue_complete": True,
                                            },
                                        ):
                                            with patch("testing.host.agent_planner.persist_agent_plan", lambda *a, **k: None):
                                                with patch("testing.host.agent_planner.load_agent_plan", lambda *a, **k: None):
                                                    streaming._run_streaming_chat(
                                                        "https://x/edit",
                                                        TITANIC_PROMPT,
                                                        1,
                                                        "s",
                                                        [],
                                                        "",
                                                        "agentic",
                                                        "agentic",
                                                        {
                                                            "history_key": "https://x/edit",
                                                            "snapshot_url": "https://x/edit",
                                                            "active_key": "1",
                                                        },
                                                    )
    assert client.chat.completions.calls >= 2
