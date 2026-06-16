"""Tests for token usage parsing and aggregation."""

import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.token_usage import (
    billable_tokens,
    extract_usage_from_response,
    format_usage_line,
    merge_usage,
    read_usage_totals,
    record_token_event,
)


def test_extract_usage_from_response_dict():
    usage = extract_usage_from_response(
        {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "total_tokens": 1050,
                "prompt_tokens_details": {"cached_tokens": 800},
            }
        }
    )
    assert usage["prompt_tokens"] == 1000
    assert usage["completion_tokens"] == 50
    assert usage["cached_tokens"] == 800
    assert usage["total_tokens"] == 1050


def test_format_usage_line_shows_cache_hit():
    line = format_usage_line({"prompt_tokens": 1000, "cached_tokens": 750, "total_tokens": 1100})
    assert "cache_hit=75%" in line


def test_record_and_read_usage_totals(tmp_path, monkeypatch):
    log_path = tmp_path / "token_usage.jsonl"
    monkeypatch.setattr("testing.host.token_usage.TOKEN_USAGE_LOG_PATH", log_path)
    record_token_event(
        session_id="sess-a",
        history_key="nb",
        mode="ask",
        usage={"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 80, "total_tokens": 110},
    )
    totals = read_usage_totals(session_id="sess-a", hours=24)
    assert totals["total_tokens"] == 110
    assert totals["cached_tokens"] == 80
    assert totals["requests"] == 1


def test_merge_usage_accumulates():
    acc = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0, "requests": 0}
    merge_usage(acc, {"prompt_tokens": 100, "completion_tokens": 5, "cached_tokens": 50, "total_tokens": 105})
    merge_usage(acc, {"prompt_tokens": 200, "completion_tokens": 10, "cached_tokens": 100, "total_tokens": 210})
    assert acc["total_tokens"] == 315
    assert acc["requests"] == 2


def test_billable_tokens_prefers_api_total():
    assert billable_tokens({"total_tokens": 99}, fallback=10) == 99
    assert billable_tokens(None, fallback=10) == 10
