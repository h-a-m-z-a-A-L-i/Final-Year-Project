"""Fire-and-forget dispatch summaries and query-only implementation guard."""

import json
import os
import sys
from unittest.mock import patch

import pytest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_action_guard import (
    batch_lacks_write_tools,
    build_query_budget_exhausted_nudge,
    build_query_only_rejection_message,
    count_implied_tool_actions,
    cumulative_has_write_tools,
    is_implementation_request,
    is_query_only_tool_batch,
    should_force_implementation_batch,
)
from testing.host.agentic_batch_executor import (
    build_fire_and_forget_user_summary,
    force_implementation_batch_from_prompt,
    prompt_requests_ml_workflow,
    should_use_batch_executor,
)


ML_PROMPT = (
    "import (/kaggle/input/datasets/codekey/zameen-com2026-16-5/zameen_master_dataset.csv) "
    "dataset and make a simple linear regression model, then make new cell and do the "
    "predictions, then make a new cell and print the models performance"
)


def test_ml_prompt_implied_actions_and_implementation():
    assert is_implementation_request(ML_PROMPT)
    assert count_implied_tool_actions(ML_PROMPT) >= 2


def test_query_only_list_cells_detected():
    assert is_query_only_tool_batch(["notebook_list_cells"])
    assert batch_lacks_write_tools(["notebook_list_cells"])


def test_query_only_rejection_message_mentions_write_tools():
    msg = build_query_only_rejection_message(ML_PROMPT, parsed_tools=["notebook_list_cells"])
    assert "insert_cell" in msg
    assert "notebook_list_cells" in msg


def test_should_use_batch_executor_for_list_cells_in_fire_and_forget():
    tool_calls = [
        {
            "id": "1",
            "function": {
                "name": "notebook_list_cells",
                "arguments": json.dumps({"url": "https://example.com/edit"}),
            },
        },
    ]
    with patch("testing.host.agentic_batch_executor.AGENTIC_FIRE_AND_FORGET", True):
        assert should_use_batch_executor(tool_calls, agentic_active=True) is True


def test_build_fire_and_forget_user_summary_from_sequential_executed():
    executed = [
        {"tool": "insert_cell", "dispatched": True, "cell_index": 35},
        {"tool": "edit_cell_by_index", "dispatched": True, "cell_index": 35},
        {"tool": "run_cell", "dispatched": True, "cell_index": 35},
    ]
    summary = build_fire_and_forget_user_summary({"executed": executed})
    assert "fire-and-forget" in summary.lower()
    assert "insert_cell" in summary
    assert "run_cell" in summary
    assert summary.strip()


def test_implementation_query_only_guard_blocks_dispatch():
    """Actionable ML workflow must not proceed with notebook_list_cells alone."""
    parsed = ["notebook_list_cells"]
    assert is_implementation_request(ML_PROMPT)
    assert is_query_only_tool_batch(parsed)
    assert batch_lacks_write_tools(parsed)
    msg = build_query_only_rejection_message(ML_PROMPT, parsed_tools=parsed)
    assert "notebook_list_cells" in msg
    assert "insert_cell" in msg
    assert "Tools ran but no summary" not in msg


def test_query_budget_blocks_second_list_cells():
    assert should_force_implementation_batch(
        prompt=ML_PROMPT,
        parsed_tools=["notebook_list_cells"],
        query_rounds_used=1,
        max_query_rounds=1,
        cumulative_has_writes=False,
    )
    assert not should_force_implementation_batch(
        prompt=ML_PROMPT,
        parsed_tools=["notebook_list_cells"],
        query_rounds_used=0,
        max_query_rounds=1,
        cumulative_has_writes=False,
    )


def test_final_round_forces_implementation_even_without_query_budget():
    assert should_force_implementation_batch(
        prompt=ML_PROMPT,
        parsed_tools=["notebook_list_cells"],
        query_rounds_used=0,
        max_query_rounds=1,
        cumulative_has_writes=False,
        round_idx=1,
        max_tool_rounds=2,
    )


def test_query_budget_nudge_mentions_write_tools():
    msg = build_query_budget_exhausted_nudge(ML_PROMPT, parsed_tools=["notebook_list_cells"])
    assert "insert_cell" in msg
    assert "Query budget exhausted" in msg


def test_ml_workflow_force_enrich_adds_insert_edit_run():
    from unittest.mock import MagicMock

    reg = MagicMock()
    reg.call.return_value = {
        "ok": True,
        "cells": [{"index": i, "type": "code"} for i in range(1, 31)],
    }
    out = force_implementation_batch_from_prompt(
        [],
        user_prompt=ML_PROMPT,
        url="https://www.kaggle.com/code/codekey/testing-ol/edit",
        tab_id=1,
        registry=reg,
    )
    names = [c.name for c in out]
    assert "insert_cell" in names
    assert "edit_cell_by_index" in names
    assert "run_cell" in names
    assert prompt_requests_ml_workflow(ML_PROMPT)


def test_cumulative_has_write_tools():
    assert cumulative_has_write_tools([{"tool": "notebook_list_cells"}]) is False
    assert cumulative_has_write_tools([{"tool": "edit_cell_by_index"}]) is True
