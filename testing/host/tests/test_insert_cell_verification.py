"""Tests for insert_cell tool (fire-and-forget dispatch)."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.insert_cell_tool import run_insert_cell


def test_run_insert_cell_dispatched():
    with patch("testing.host.insert_cell_tool.execute_bot_command") as mock_exec, patch(
        "testing.host.insert_cell_tool.time.sleep"
    ):
        mock_exec.side_effect = [
            {"ok": True, "result": {"ok": True, "dispatched": True}},
            {"ok": True, "result": {"ok": True, "dispatched": True}},
        ]
        out = run_insert_cell(
            {
                "url": "https://example.com/edit",
                "index": 2,
                "direction": "below",
            }
        )
    assert out["ok"] is True
    assert out["dispatched"] is True
    assert mock_exec.call_count == 2
    assert mock_exec.call_args_list[0][0][0]["fire_and_forget"] is True
    assert mock_exec.call_args_list[1][0][0]["action"] == "click_selector"
