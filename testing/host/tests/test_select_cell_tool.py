"""Tests for select_cell dispatch + verify."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.bot_command import execute_bot_command_sync
from testing.host.select_cell_tool import run_select_cell


def test_select_fire_and_forget_does_not_wait_for_extension():
    with patch("testing.host.bot_command._send_msg") as mock_send:
        out = execute_bot_command_sync(
            {
                "action": "select_cell_by_index",
                "url": "https://example.com/edit",
                "cell_index": 2,
                "fire_and_forget": True,
                "wait_for_result": False,
            },
            timeout=0.1,
        )
    assert out["ok"] is True
    assert out["result"]["dispatched"] is True
    mock_send.assert_called_once()


def test_run_select_cell_success():
    with patch("testing.host.select_cell_tool.dispatch_select_cell") as mock_dispatch:
        mock_dispatch.return_value = {"ok": True, "result": {"ok": True, "dispatched": True}}
        out = run_select_cell({"url": "https://example.com/edit", "cell_index": 2})
    assert out["ok"] is True
    assert out["tool"] == "select_cell_by_index"
    assert out["cell_index"] == 2
    assert out["dom_index"] == 1
    assert out["dispatched"] is True
    assert out["phase"] == "dispatched"


def test_run_select_cell_dispatch_failed():
    with patch("testing.host.select_cell_tool.dispatch_select_cell") as mock_dispatch:
        mock_dispatch.return_value = {"ok": False, "error": "dispatch failed"}
        out = run_select_cell({"url": "https://example.com/edit", "cell_index": 2})
    assert out["ok"] is False
    assert out["phase"] == "select_dispatch_failed"
