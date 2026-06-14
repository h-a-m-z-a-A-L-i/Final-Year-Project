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
            },
            timeout=0.1,
        )
    assert out["ok"] is True
    assert out["result"]["dispatched"] is True
    mock_send.assert_called_once()


def test_run_select_cell_success():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.return_value = {
            "ok": True,
            "result": {
                "ok": True,
                "dispatched": True,
                "domIndex": 1,
                "appIndex": 2,
                "phase": "dispatched",
            },
        }
        out = run_select_cell({"url": "https://example.com/edit", "cell_index": 2})
    assert out["ok"] is True
    assert out["tool"] == "select_cell_by_index"
    assert out["cell_index"] == 2
    assert out["dom_index"] == 1
