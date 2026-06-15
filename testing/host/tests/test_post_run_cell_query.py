"""Tests for post-run cell query and multi-tool queue ordering."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agent_goal_verification import verify_run_cell
from testing.host.agentic_batch_executor import (
    ParsedToolCall,
    _build_cell_evidence_entry,
    _sort_tool_calls,
    fetch_queue_cell_evidence,
    partition_batch,
)
from testing.host.local_notebook_tools import notebook_get_cell


NOTEBOOK_PATH = os.path.join(
    repo_root,
    "testing",
    "host",
    "data",
    "notebooks",
    "persistent",
    "https___www_kaggle_com_code_codekey_testing_ol_edit.json",
)


def test_interleaved_multi_cell_batch_not_deferred():
    """insert/edit/run per cell interleaved → sort → all execute in one batch."""
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 10, "direction": "below"}),
        ParsedToolCall("2", "edit_cell_by_index", {"cell_index": 11, "content": "print(1)"}),
        ParsedToolCall("3", "run_cell", {"cell_index": 11}),
        ParsedToolCall("4", "insert_cell", {"index": 11, "direction": "below"}),
        ParsedToolCall("5", "edit_cell_by_index", {"cell_index": 12, "content": "print(2)"}),
        ParsedToolCall("6", "run_cell", {"cell_index": 12}),
    ]
    sorted_calls = _sort_tool_calls(calls)
    execute, deferred = partition_batch(sorted_calls)
    assert deferred == []
    assert len(execute) == 6
    names = [c.name for c in execute]
    assert names.count("insert_cell") == 2
    assert names.count("edit_cell_by_index") == 2
    assert names.count("run_cell") == 2
    last_edit = max(i for i, n in enumerate(names) if n == "edit_cell_by_index")
    first_run = names.index("run_cell")
    assert first_run > last_edit


def test_fetch_queue_cell_evidence_merges_run_wait_success():
    registry = MagicMock()
    registry.call = lambda name, args: {
        "ok": True,
        "cell": {
            "index": args["cell_index"],
            "input": "df['price']",
            "output": "ok\n",
            "execution_order": 15,
            "type": "code",
        },
    }
    waits = [
        {
            "ok": True,
            "cell_index": 31,
            "run_verified": True,
            "run_succeeded": True,
            "output": "ok\n",
            "execution_order": 15,
            "wait_reason": "execution_order_increased",
        }
    ]
    out = fetch_queue_cell_evidence(registry, "https://x/edit", [31], run_waits=waits)
    cell = out["cells"][0]
    assert cell["run_verified"] is True
    assert cell["success"] is True
    assert cell["input"] == "df['price']"
    assert "ok" in cell["output"]


def test_fetch_queue_cell_evidence_merges_run_wait_error():
    registry = MagicMock()
    registry.call = lambda name, args: {
        "ok": True,
        "cell": {
            "index": 31,
            "input": "df['price']",
            "output": "KeyError: 'price'\n",
            "execution_order": 16,
        },
    }
    waits = [
        {
            "ok": True,
            "cell_index": 31,
            "run_verified": True,
            "run_succeeded": False,
            "has_error": True,
            "error_type": "KeyError",
            "error_summary": "KeyError: 'price'",
            "output": "KeyError: 'price'\n",
        }
    ]
    out = fetch_queue_cell_evidence(registry, "https://x/edit", [31], run_waits=waits)
    cell = out["cells"][0]
    assert cell["run_verified"] is True
    assert cell["success"] is False
    assert cell["has_error"] is True
    assert "KeyError" in str(cell["traceback"])


def test_fetch_queue_cell_evidence_batch_get_cells():
    registry = MagicMock()

    def _call(name, args):
        if name == "notebook_get_cells":
            return {
                "ok": True,
                "cells": [
                    {"index": 1, "input": "a=1", "output": "", "execution_order": 3},
                    {"index": 2, "input": "print(2)", "output": "2\n", "execution_order": 4},
                ],
            }
        return {"ok": False}

    registry.call = _call
    waits = [
        {"ok": True, "cell_index": 1, "run_verified": True, "run_succeeded": True, "output": ""},
        {"ok": True, "cell_index": 2, "run_verified": True, "run_succeeded": True, "output": "2\n"},
    ]
    out = fetch_queue_cell_evidence(registry, "https://x/edit", [1, 2], run_waits=waits)
    assert out["count"] == 2
    assert out["cells"][0]["run_verified"] is True
    assert out["cells"][1]["success"] is True


def test_verify_run_cell_silent_success_when_run_verified():
    rec = verify_run_cell(
        10,
        run_wait={
            "ok": True,
            "run_verified": True,
            "run_succeeded": True,
            "output": "",
            "run_completed": True,
        },
    )
    assert rec["verification_status"] == "verified"
    assert rec["evidence"]["run_verified"] is True


def test_verify_run_cell_fails_when_not_run_verified():
    rec = verify_run_cell(
        31,
        run_wait={"ok": True, "run_verified": False, "output": ""},
    )
    assert rec["verification_status"] == "failed"
    assert "not verified" in rec["reason"].lower()


def test_build_cell_evidence_entry_from_wait_only():
    entry = _build_cell_evidence_entry(
        31,
        {"input": "x=1", "output": "err", "execution_order": 5},
        {
            "ok": True,
            "run_verified": True,
            "run_succeeded": False,
            "has_error": True,
            "error_summary": "NameError: x",
            "output": "NameError: x\n",
        },
    )
    assert entry["run_verified"] is True
    assert entry["success"] is False
    assert "NameError" in str(entry["traceback"])


def test_live_notebook_get_cell_returns_state():
    if not os.path.isfile(NOTEBOOK_PATH):
        return
    with open(NOTEBOOK_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    cells = data.get("cells") or []
    if not cells:
        return
    idx = int(cells[0]["index"])
    url = data.get("tabUrl") or "https://www.kaggle.com/code/codekey/testing-ol/edit"
    result = notebook_get_cell({"url": url, "cell_index": idx, "include_output": True})
    assert result.get("ok") is True
    cell = result.get("cell") or result
    assert cell.get("input") is not None or cell.get("index") == idx
