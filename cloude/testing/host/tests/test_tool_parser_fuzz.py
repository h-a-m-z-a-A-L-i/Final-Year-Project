"""Fuzz tests for <agent_tool_batch> parser acceptance (formats A–F + Cell 30 variants)."""

from __future__ import annotations

import json
import os
import sys

import pytest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_text_tools import parse_text_tool_batch_result
from testing.host.tool_parser_diagnostics import (
    derive_parser_reason,
    diagnose_text_tool_parse,
)

_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"
_TOOL_A = {"tool": "run_cell", "args": {"cell_index": 1, "url": _URL}}
_TOOL_B = {"tool": "notebook_get_cell", "args": {"cell_index": 30, "url": _URL}}


def _batch(*tools: dict) -> str:
    return (
        "<agent_tool_batch>\n"
        + json.dumps(list(tools), indent=2)
        + "\n</agent_tool_batch>"
    )


def _diag(text: str) -> dict:
    return diagnose_text_tool_parse(text)


def _reason(text: str) -> str:
    d = _diag(text)
    return derive_parser_reason(d, parse_result=parse_text_tool_batch_result(text))


# --- Format A: clean batch ---


def test_format_a_clean_batch():
    text = _batch(_TOOL_A)
    pr = parse_text_tool_batch_result(text)
    d = _diag(text)
    assert pr.tool_calls
    assert d["opening_tag_found"] and d["closing_tag_found"]
    assert d["json_parse_success"]
    assert d["tool_count_detected"] == len(pr.tool_calls)


# --- Format B: prose before batch ---


def test_format_b_prose_before_batch():
    text = f"Sure, running cell 1.\n{_batch(_TOOL_A)}"
    pr = parse_text_tool_batch_result(text)
    d = _diag(text)
    assert len(pr.tool_calls) == 1
    assert d["tool_count_detected"] == 1
    assert _reason(text) == "PARSE_OK"


# --- Format C: non-array comma-separated objects ---


def test_format_c_non_array_json_recovered():
    text = (
        "<agent_tool_batch>\n"
        '{"tool": "run_cell", "args": {"cell_index": 1, "url": "' + _URL + '"}},'
        '{"tool": "run_cell", "args": {"cell_index": 2, "url": "' + _URL + '"}}\n'
        "</agent_tool_batch>"
    )
    pr = parse_text_tool_batch_result(text)
    d = _diag(text)
    assert len(pr.tool_calls) == 2
    assert d["json_parse_success"] is True
    assert "non_array_wrap" in pr.recovery_methods


def test_format_c_single_object_accepted():
    text = (
        "<agent_tool_batch>\n"
        '{"tool": "run_cell", "args": {"cell_index": 1, "url": "' + _URL + '"}}\n'
        "</agent_tool_batch>"
    )
    pr = parse_text_tool_batch_result(text)
    assert len(pr.tool_calls) == 1


# --- Format D: markdown fences ---


def test_format_d_fence_without_tags():
    text = 'Here:\n```json\n[{"tool": "run_cell", "args": {"cell_index": 1, "url": "' + _URL + '"}}]\n```'
    pr = parse_text_tool_batch_result(text)
    d = _diag(text)
    assert len(pr.tool_calls) == 1
    assert d["fence_fallback_used"] is True


def test_format_d_fence_around_batch_tags():
    text = (
        "```\n"
        + _batch(_TOOL_A)
        + "\n```"
    )
    pr = parse_text_tool_batch_result(text)
    d = _diag(text)
    # Tags inside fence: batch regex still matches outer text
    assert len(pr.tool_calls) == 1
    assert d["opening_tag_found"]


# --- Format E: multiple batches ---


def test_format_e_multiple_batches_merged():
    text = _batch(_TOOL_A) + "\n" + _batch({"tool": "run_cell", "args": {"cell_index": 2, "url": _URL}})
    pr = parse_text_tool_batch_result(text)
    d = _diag(text)
    assert len(pr.tool_calls) == 2
    assert d["multiple_batches"] is True
    assert d["batch_count"] == 2


# --- Format F: reasoning before and after ---


def test_format_f_reasoning_before_and_after():
    text = (
        "We need to fetch cell 24."
        + _batch(_TOOL_B)
        + "The error in Cell 30 occurs because..."
    )
    pr = parse_text_tool_batch_result(text)
    d = _diag(text)
    assert len(pr.tool_calls) == 1
    assert _reason(text) == "PARSE_OK"


# --- Cell 30 live failure replays ---


def test_cell30_round0_bare_json_no_tags():
    """GPT-OSS emitted JSON object without <agent_tool_batch> wrapper."""
    text = (
        'We will edit cell 30 accordingly and run.'
        '{"tool": "edit_cell_by_index", "arguments": {"cell_index": 30, "url": "' + _URL + '"}}'
    )
    pr = parse_text_tool_batch_result(text, action_required=True)
    assert len(pr.tool_calls) == 1
    assert pr.recovery_used is True
    assert "bare_json" in pr.recovery_methods


def test_cell30_round1_unclosed_batch_tag():
    """GPT-OSS opened batch, emitted valid JSON array, but omitted </agent_tool_batch>."""
    text = (
        "We need to edit cell 30."
        '<agent_tool_batch>[{"tool":"insert_cell","args":{"cell_index":30}},'
        '{"tool":"run_cell","args":{"cell_index":31}}]'
    )
    pr = parse_text_tool_batch_result(text, action_required=True)
    assert len(pr.tool_calls) >= 1
    assert pr.recovery_used is True
    assert "unclosed_tag" in pr.recovery_methods


def test_cell30_non_array_with_tool_name_alias():
    """GPT-OSS used tool_name + comma-separated objects inside closed batch."""
    text = (
        "We need to run a cell.<agent_tool_batch>\n"
        '{\n  "tool_name": "edit_cell_by_index",\n'
        '  "arguments": {"cell_index": 30, "url": "' + _URL + '"}\n'
        "}\n"
        '{\n  "tool_name": "run_cell",\n'
        '  "arguments": {"cell_index": 30, "url": "' + _URL + '"}\n'
        "}\n"
        "</agent_tool_batch>"
    )
    pr = parse_text_tool_batch_result(text)
    assert len(pr.tool_calls) == 2
    assert "non_array_wrap" in pr.recovery_methods


# --- Unicode quotes ---


def test_unicode_smart_quotes_recovered():
    text = (
        "<agent_tool_batch>\n"
        "[{\"tool\": “run_cell”, \"args\": {\"cell_index\": 1, \"url\": \"" + _URL + "\"}}]\n"
        "</agent_tool_batch>"
    )
    pr = parse_text_tool_batch_result(text)
    assert len(pr.tool_calls) == 1
    assert "smart_quotes" in pr.recovery_methods


# --- Unknown tools ---


def test_unknown_tool_filtered_to_zero():
    text = _batch({"tool": "fly_to_moon", "args": {}})
    d = _diag(text)
    pr = parse_text_tool_batch_result(text)
    assert len(pr.tool_calls) == 0
    assert "fly_to_moon" in d["unknown_tools"]
    assert _reason(text) == "UNKNOWN_TOOL_ONLY"
