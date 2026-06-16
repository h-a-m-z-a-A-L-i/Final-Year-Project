"""Integration tests for tolerant tool batch parser recovery."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_text_tools import parse_text_tool_batch_result
from testing.host.tool_parser_recovery import normalize_smart_quotes

_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"
REFUSAL_LOG = Path(__file__).resolve().parents[1] / "data" / "logs" / "agent_tool_refusal.jsonl"


def _parse(text: str, *, action_required: bool = True):
    return parse_text_tool_batch_result(text, action_required=action_required)


# --- A. missing closing tag ---


def test_recovery_unclosed_batch_tag():
    text = (
        "We need to edit cell 30."
        '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":31,"url":"' + _URL + '"}}]'
    )
    result = _parse(text)
    assert len(result.tool_calls) == 1
    assert result.recovery_used is True
    assert "unclosed_tag" in result.recovery_methods


# --- B. smart quotes ---


def test_recovery_smart_quotes():
    text = (
        "<agent_tool_batch>\n"
        '[{"tool": “run_cell”, "args": {"cell_index": 1, "url": "' + _URL + '"}}]\n'
        "</agent_tool_batch>"
    )
    result = _parse(text)
    assert len(result.tool_calls) == 1
    assert result.recovery_used is True
    assert "smart_quotes" in result.recovery_methods


def test_normalize_smart_quotes_unit():
    raw = '{"tool": “run_cell”}'
    normalized, changed = normalize_smart_quotes(raw)
    assert changed is True
    assert "\u201c" not in normalized


# --- C. non-array JSON ---


def test_recovery_non_array_comma_objects():
    text = (
        "<agent_tool_batch>\n"
        '{"tool": "edit_cell_by_index", "args": {"cell_index": 30, "url": "' + _URL + '"}},\n'
        '{"tool": "run_cell", "args": {"cell_index": 30, "url": "' + _URL + '"}}\n'
        "</agent_tool_batch>"
    )
    result = _parse(text)
    assert len(result.tool_calls) == 2
    assert result.recovery_used is True
    assert "non_array_wrap" in result.recovery_methods


def test_recovery_tool_name_alias_non_array():
    text = (
        "<agent_tool_batch>\n"
        '{"tool_name": "edit_cell_by_index", "arguments": {"cell_index": 30, "url": "' + _URL + '"}},\n'
        '{"tool_name": "run_cell", "arguments": {"cell_index": 30, "url": "' + _URL + '"}}\n'
        "</agent_tool_batch>"
    )
    result = _parse(text)
    assert len(result.tool_calls) == 2
    assert "non_array_wrap" in result.recovery_methods


# --- D. bare JSON ---


def test_recovery_bare_json_object():
    text = (
        "We will edit cell 30."
        '{"tool": "edit_cell_by_index", "arguments": {"cell_index": 30, "url": "' + _URL + '"}}'
    )
    result = _parse(text, action_required=True)
    assert len(result.tool_calls) == 1
    assert result.recovery_used is True
    assert "bare_json" in result.recovery_methods


def test_bare_json_not_recovered_without_action_required():
    text = '{"tool": "run_cell", "args": {"cell_index": 1, "url": "' + _URL + '"}}'
    result = _parse(text, action_required=False)
    assert len(result.tool_calls) == 0
    assert result.recovery_used is False


# --- E. prose + malformed batch ---


def test_recovery_prose_plus_unclosed_batch():
    text = (
        "First inspect columns."
        '<agent_tool_batch>[{"tool":"insert_cell","args":{"cell_index":30}},'
        '{"tool":"run_cell","args":{"cell_index":31}}]'
    )
    result = _parse(text)
    assert len(result.tool_calls) >= 1
    assert result.recovery_used is True


def test_prose_only_still_fails():
    text = "I will fix cell 30 manually by editing the histogram code."
    result = _parse(text)
    assert len(result.tool_calls) == 0


# --- F. Cell 30 real-world outputs ---


def _cell30_refusal_samples() -> list[dict]:
    if not REFUSAL_LOG.exists():
        return []
    out: list[dict] = []
    for line in REFUSAL_LOG.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if "cell 30" not in str(row.get("goal", "")).lower():
            continue
        if row.get("source") != "react_round":
            continue
        if int(row.get("parsed_tool_count") or 0) > 0:
            continue
        out.append(row)
    return out


@pytest.mark.parametrize(
    "sample_idx",
    [0, 1, 2],
    ids=["cell30_round0_bare", "cell30_round1_unclosed", "cell30_tool_name_non_array"],
)
def test_cell30_refusal_samples_parse(sample_idx: int):
    samples = _cell30_refusal_samples()
    if sample_idx >= len(samples):
        pytest.skip("Cell 30 refusal samples not available")
    raw = str(samples[sample_idx]["raw_model_response"])
    result = _parse(raw, action_required=True)
    assert len(result.tool_calls) >= 1, (
        f"expected recovery for sample {sample_idx}, "
        f"errors={result.parse_errors}, methods={result.recovery_methods}"
    )
    assert result.recovery_used is True


# --- Safety: no arbitrary JSON recovery ---


def test_no_recovery_arbitrary_json():
    text = 'Config loaded: {"version": 1, "args": {"debug": true}}'
    result = _parse(text, action_required=True)
    assert len(result.tool_calls) == 0


def test_no_recovery_tool_without_args():
    text = '{"tool": "run_cell"}'
    result = _parse(text, action_required=True)
    assert len(result.tool_calls) == 0


def test_no_recovery_unknown_tool_bare_json():
    text = '{"tool": "fly_to_moon", "args": {"cell_index": 1}}'
    result = _parse(text, action_required=True)
    assert len(result.tool_calls) == 0
    assert "fly_to_moon" in result.unknown_tools
