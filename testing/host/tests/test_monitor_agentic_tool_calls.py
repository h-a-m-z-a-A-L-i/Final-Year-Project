#!/usr/bin/env python3
"""Smoke test for monitor_agentic_tool_calls formatter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[3]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from testing.host.scripts.monitor_agentic_tool_calls import (  # noqa: E402
    _fmt_line,
    _slug_matches,
)


def test_fmt_exec_line():
    row = {
        "event": "exec",
        "local_time": "12:00:00",
        "tool": "edit_cell_by_index",
        "args": {"cell_index": 1, "content": "x = 1"},
        "result": {"ok": True},
        "ok": True,
        "phase": "batch",
    }
    text = _fmt_line(row, use_color=False)
    assert "EXEC" in text
    assert "edit_cell_by_index" in text
    assert "OK" in text
    assert "payload:" in text
    assert "cell_index: 1" in text
    assert "result:" in text


def test_fmt_parse_line():
    row = {
        "event": "parse",
        "local_time": "12:00:01",
        "round": 0,
        "tools": ["insert_cell", "edit_cell_by_index"],
        "tool_count": 2,
        "source": "native",
    }
    text = _fmt_line(row, use_color=False)
    assert "PARSE" in text
    assert "insert_cell" in text


def test_fmt_dispatch_and_result():
    dispatch = {
        "event": "dispatch",
        "local_time": "12:00:02",
        "tool": "run_cell",
        "args": {"cell_index": 3, "tab_id": 2015941739},
        "round": 1,
        "phase": "run_queue",
    }
    result = {
        "event": "result",
        "local_time": "12:00:03",
        "tool": "run_cell",
        "args": {"cell_index": 3, "tab_id": 2015941739},
        "result": {"ok": False, "error": "timeout"},
        "ok": False,
        "round": 1,
        "phase": "run_queue",
        "error": "timeout",
    }
    d_text = _fmt_line(dispatch, use_color=False)
    r_text = _fmt_line(result, use_color=False)
    assert "CALL" in d_text
    assert "run_cell" in d_text
    assert "payload:" in d_text
    assert "cell_index: 3" in d_text
    assert "tab_id: 2015941739" in d_text
    assert "RESULT" in r_text
    assert "FAIL" in r_text
    assert "timeout" in r_text
    assert "result:" in r_text


def test_fmt_tool_specific_payload_fields():
    delete_call = {
        "event": "dispatch",
        "local_time": "03:37:41",
        "tool": "delete_by_index",
        "args": {"cell_index": 2, "tab_id": 2015941739},
        "round": 0,
        "phase": "write",
    }
    insert_call = {
        "event": "dispatch",
        "local_time": "03:37:42",
        "tool": "insert_cell",
        "args": {"index": 1, "direction": "below", "content": "print('hi')"},
        "round": 0,
        "phase": "write",
    }
    delete_text = _fmt_line(delete_call, use_color=False)
    insert_text = _fmt_line(insert_call, use_color=False)
    assert "delete_by_index" in delete_text
    assert "cell_index: 2" in delete_text
    assert "index: 1" in insert_text
    assert "direction: \"below\"" in insert_text
    assert "print('hi')" in insert_text


def test_fmt_verbose_shows_full_args():
    row = {
        "event": "dispatch",
        "local_time": "12:00:04",
        "tool": "edit_cell_by_index",
        "args": {"cell_index": 0, "content": "print(1)"},
        "round": 0,
    }
    plain = _fmt_line(row, use_color=False, verbose=False)
    verbose = _fmt_line(row, use_color=False, verbose=True)
    assert "payload:" in plain
    assert "cell_index: 0" in plain
    assert "print(1)" in plain
    assert "payload:" in verbose
    assert '"cell_index": 0' in verbose
    assert '"content": "print(1)"' in verbose


def test_slug_matches_filter():
    row = {"notebook_slug": "testing-ol", "url": "https://www.kaggle.com/code/u/testing-ol/edit"}
    assert _slug_matches(row, "testing-ol")
    assert _slug_matches(row, "testing")
    assert not _slug_matches(row, "other-notebook")


def test_fmt_batch_end():
    row = {
        "event": "batch_end",
        "local_time": "12:00:05",
        "round": 2,
        "batch_id": "r2",
        "ok": True,
        "detail": "goal met",
    }
    text = _fmt_line(row, use_color=False)
    assert "BATCH" in text
    assert "r2" in text
    assert "done OK" in text
