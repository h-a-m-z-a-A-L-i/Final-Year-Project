"""Tests for tool_parser_diagnostics instrumentation."""

from __future__ import annotations

import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import tool_parser_diagnostics as tpd


def test_diagnose_unclosed_batch():
    text = '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":1}}]'
    d = tpd.diagnose_text_tool_parse(text)
    assert d["opening_tag_found"] is True
    assert d["closing_tag_found"] is False
    assert d["unclosed_batch_tag"] is True
    assert d["tool_count_detected"] == 1


def test_build_failure_record_fields():
    raw = "prose only"
    rec = tpd.build_parser_failure_record(
        goal="fix cell 30",
        round_idx=0,
        raw_output=raw,
        action_required=True,
    )
    assert rec["parsed_tool_count"] == 0
    assert rec["raw_output"] == raw
    assert rec["parser_reason"]
    assert len(rec["first_500_chars"]) <= 500
    assert "diagnostics" in rec
    diag = rec["diagnostics"]
    for key in (
        "batch_tag_found",
        "opening_tag_found",
        "closing_tag_found",
        "json_array_found",
        "json_parse_success",
        "tool_count_detected",
    ):
        assert key in diag


def test_append_parser_failure_record(tmp_path, monkeypatch):
    log_path = tmp_path / "failures.jsonl"
    monkeypatch.setattr(tpd, "PARSER_FAILURE_LOG", log_path)
    rec = tpd.build_parser_failure_record(
        goal="test",
        round_idx=0,
        raw_output="<agent_tool_batch>[]</agent_tool_batch>",
        action_required=True,
    )
    tpd.append_parser_failure_record(rec)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["goal"] == "test"
