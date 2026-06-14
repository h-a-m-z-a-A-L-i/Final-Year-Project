"""Tests for reliability improvements (prose cap, multi-batch, unknown tools, compression, metrics)."""

import json
import os
import sys
from unittest.mock import patch

import pytest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import testing.host.streaming as streaming
from testing.host.agent_state import (
    COMPLETED_STEPS_COMPRESS_AFTER,
    empty_agent_state,
    format_agent_state_block,
    update_agent_state_from_verification,
)
from testing.host.agentic_action_guard import (
    MAX_PROSE_ONLY_ROUNDS,
    build_prose_only_corrective_nudge,
    build_prose_only_exhausted_message,
)
from testing.host.agentic_mode import set_dashboard_agentic_enabled
from testing.host.agentic_text_tools import parse_text_tool_batch, parse_text_tool_batch_result
from testing.host.agentic_text_tools_types import build_unknown_tools_nudge
from testing.host.agent_metrics import METRICS_PATH, read_metrics, record_turn_metric


def test_prose_only_max_two_wasted_calls():
    assert MAX_PROSE_ONLY_ROUNDS == 2
    msg = build_prose_only_exhausted_message("edit cell 1", streak=2)
    assert "stopped" in msg.lower()
    assert "agent_tool_batch" in msg
    nudge = build_prose_only_corrective_nudge("edit cell 1", streak=1, use_text_tools=True)
    assert "MUST emit" in nudge


def test_multiple_batches_merged_not_lost():
    text = (
        '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":1}}]</agent_tool_batch>'
        '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":2}}]</agent_tool_batch>'
    )
    result = parse_text_tool_batch_result(text)
    assert result.multiple_batches is True
    assert result.batch_count == 2
    assert len(result.tool_calls) == 2
    indices = sorted(
        json.loads(tc["function"]["arguments"])["cell_index"] for tc in result.tool_calls
    )
    assert indices == [1, 2]


def test_unknown_tools_reported():
    text = '<agent_tool_batch>[{"tool":"fly_to_moon","args":{}}]</agent_tool_batch>'
    result = parse_text_tool_batch_result(text)
    assert result.tool_calls == []
    assert result.unknown_tools == ["fly_to_moon"]
    nudge = build_unknown_tools_nudge(result)
    assert "fly_to_moon" in nudge
    assert "Unknown tools" in nudge


def test_mixed_valid_and_unknown_tools():
    text = (
        '<agent_tool_batch>['
        '{"tool":"run_cell","args":{"cell_index":1}},'
        '{"tool":"fly_to_moon","args":{}}'
        ']</agent_tool_batch>'
    )
    result = parse_text_tool_batch_result(text)
    assert len(result.tool_calls) == 1
    assert "fly_to_moon" in result.unknown_tools


def test_completed_steps_compression():
    state = empty_agent_state(goal="pipeline")
    executed = [{"tool": f"step_{i}", "cell_index": i} for i in range(15)]
    v = {"executed": executed, "verified": True}
    for _ in range(15):
        state = update_agent_state_from_verification(state, v, goal="pipeline")
    block = format_agent_state_block(state)
    assert "15 steps completed" in block
    assert "Recent:" in block
    assert len(state.get("completed_steps") or []) <= COMPLETED_STEPS_COMPRESS_AFTER


def test_metrics_record_and_rates(tmp_path):
    metrics_file = tmp_path / "agent_metrics.json"
    with patch("testing.host.agent_metrics.METRICS_PATH", metrics_file):
        data = record_turn_metric(
            event="test",
            increment={
                "turns_total": 2,
                "prose_only_events": 1,
                "tool_batch_parse_attempts": 4,
                "tool_batch_parse_success": 3,
            },
        )
        assert metrics_file.is_file()
        assert data["prose_only_events"] == 1
        assert data["rates"]["prose_only_rate"] == 0.5
        assert data["rates"]["tool_batch_parse_rate"] == 0.75
        loaded = read_metrics()
        assert loaded["turns_total"] == 2


class _FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self):
        return {"content": self.content, "tool_calls": self.tool_calls}


class _FakeChoice:
    def __init__(self, message):
        self.message = message

    def model_dump(self):
        return {"message": self.message.model_dump()}


class _FakeResponse:
    def __init__(self, content=""):
        self.choices = [_FakeChoice(_FakeMessage(content))]

    def model_dump(self):
        return {"choices": [c.model_dump() for c in self.choices]}


class _ProseThenStopClient:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _FakeResponse("I think you should edit cell 10 manually.")
        return _FakeResponse("Still no tools, just advice.")


def test_prose_only_stops_after_two_calls_not_seven():
    os.environ["AGENTIC_TEXT_TOOLS"] = "1"
    set_dashboard_agentic_enabled(True)
    client = type("C", (), {"chat": type("Ch", (), {"completions": _ProseThenStopClient()})})()
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
                                        streaming._run_streaming_chat(
                                            "https://x/edit",
                                            "Edit cell 10 to print(1) and run it",
                                            1,
                                            "s",
                                            [],
                                            "",
                                            "agentic",
                                            "agentic",
                                            {"history_key": "https://x/edit", "snapshot_url": "https://x/edit", "active_key": "1"},
                                        )
    assert client.chat.completions.calls <= MAX_PROSE_ONLY_ROUNDS + 1


def test_parse_text_tool_batch_backward_compat():
    text = '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":3}}]</agent_tool_batch>'
    assert len(parse_text_tool_batch(text)) == 1
