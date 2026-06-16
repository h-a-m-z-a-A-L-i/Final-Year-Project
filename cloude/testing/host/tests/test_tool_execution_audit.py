"""Tests for tool execution audit classification."""

from __future__ import annotations

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.tool_execution_audit import (
    build_batch_record,
    classify_failure_case,
    infer_pipeline_stop_stage,
)


def test_case_a_parsed_not_dispatched():
    batch = build_batch_record(
        session_id="s1",
        round_index=0,
        parsed_tool_count=2,
        dispatcher_received=False,
    )
    assert classify_failure_case(batch) == "CASE_A"


def test_case_e_verification_fail_success_claimed():
    batch = build_batch_record(
        session_id="s1",
        round_index=1,
        parsed_tool_count=2,
        dispatcher_received=True,
        executor_called=True,
        executor_ok=True,
        verification_received=True,
        verification_success=False,
        assistant_claimed_success=True,
    )
    assert classify_failure_case(batch) == "CASE_E"


def test_infer_stop_stage():
    batch = build_batch_record(session_id="s1", round_index=0, parsed_tool_count=0)
    batch["failure_case"] = classify_failure_case(batch)
    assert infer_pipeline_stop_stage(batch) == "parse_zero_tools"
