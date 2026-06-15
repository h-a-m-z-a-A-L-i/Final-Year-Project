#!/usr/bin/env python3
"""Smoke test for monitor_agentic_tool_calls formatter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[3]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from testing.host.scripts.monitor_agentic_tool_calls import _fmt_line  # noqa: E402


def test_fmt_exec_line():
    row = {
        "event": "exec",
        "local_time": "12:00:00",
        "tool": "edit_cell_by_index",
        "args": {"cell_index": 1, "content": "x = 1"},
        "ok": True,
        "phase": "batch",
    }
    text = _fmt_line(row, use_color=False)
    assert "EXEC" in text
    assert "edit_cell_by_index" in text
    assert "OK" in text


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
