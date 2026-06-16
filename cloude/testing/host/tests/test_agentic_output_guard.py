"""Tests for agentic output guard and Cerebras transient retry."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_output_guard import (
    contains_manual_code_without_tools,
    should_reject_prose_final,
)
from testing.host.cerebras_client import (
    CerebrasClientRouter,
    _is_transient_error,
    reset_cerebras_key_state_for_tests,
)
from testing.host.execution_integrity import (
    ExecutionIntegrityState,
    apply_final_integrity_gate,
)


def test_contains_manual_code_without_tools():
    text = "```python\nimport pandas as pd\ndf['price']\n```"
    assert contains_manual_code_without_tools(text) is True
    batch = '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":30}}]</agent_tool_batch>'
    assert contains_manual_code_without_tools(batch) is False


def test_should_reject_prose_when_not_verified():
    assert should_reject_prose_final(
        "The error is fixed.",
        prompt="Fix error in cell 30 and run it",
        verification={"strict_goal_verified": False, "continue_react_loop": True},
        tools_executed=2,
    ) is True


def test_should_allow_summary_when_strict_verified():
    assert should_reject_prose_final(
        "Cell 30 runs successfully.",
        prompt="Fix error in cell 30",
        verification={"strict_goal_verified": True},
        tools_executed=2,
    ) is False


def test_integrity_gate_skips_on_llm_failure():
    state = ExecutionIntegrityState()
    text = "LLM request failed: Connection error."
    out, blocked = apply_final_integrity_gate(
        text,
        state,
        verification=None,
        action_required=True,
        llm_request_failed=True,
    )
    assert out == text
    assert blocked is False


def test_transient_error_detection():
    assert _is_transient_error(Exception("Connection error.")) is True
    assert _is_transient_error(Exception("Request timed out")) is True
    assert _is_transient_error(Exception("token_quota_exceeded")) is False


@patch("testing.host.cerebras_client.time.sleep")
def test_cerebras_retries_transient_error(mock_sleep):
    reset_cerebras_key_state_for_tests()
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        Exception("Connection error."),
        {"ok": True},
    ]

    with patch("testing.host.cerebras_client.Cerebras", return_value=client):
        router = CerebrasClientRouter(primary_key="pk", secondary_key="", profile="primary")
        result = router.create_completion(model="m", messages=[])

    assert result == {"ok": True}
    assert client.chat.completions.create.call_count == 2
    mock_sleep.assert_called_once()
