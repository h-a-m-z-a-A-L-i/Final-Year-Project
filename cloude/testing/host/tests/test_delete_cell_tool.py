import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.bot_command import _normalize_action, execute_bot_command_sync
from testing.host.delete_cell_tool import run_delete_cell
from testing.host import bot_tool_utils as btu


def test_normalize_delete_cell_alias():
    assert _normalize_action({"action": "delete_cell"}) == "delete_by_index"
    assert _normalize_action({"action": "delete_by_index"}) == "delete_by_index"


def test_normalize_delete_cell_args():
    cmd, err = btu.normalize_delete_cell_args(
        {"url": "https://example.com/edit", "cell_index": 5}
    )
    assert err is None
    assert cmd["action"] == "delete_by_index"
    assert cmd["cell_index"] == 5
    assert cmd["cellIndex"] == 4


def test_normalize_delete_cell_args_propagates_fire_and_forget():
    cmd, err = btu.normalize_delete_cell_args(
        {"url": "https://example.com/edit", "cell_index": 5, "fire_and_forget": True}
    )
    assert err is None
    assert cmd["fire_and_forget"] is True
    assert cmd["wait_for_result"] is False


def test_run_delete_cell_success():
    with patch("testing.host.delete_cell_tool.execute_bot_command") as mock_exec, patch(
        "testing.host.delete_cell_tool.time.sleep"
    ):
        mock_exec.side_effect = [
            {"ok": True, "result": {"ok": True, "dispatched": True}},
            {"ok": True, "result": {"ok": True, "dispatched": True}},
        ]
        out = run_delete_cell({"url": "https://example.com/edit", "cell_index": 5})
    assert out["ok"] is True
    assert out["tool"] == "delete_by_index"
    assert out["cell_index"] == 5
    assert out["phase"] == "dispatched"
    assert out["dispatched"] is True
    assert mock_exec.call_count == 2
    assert mock_exec.call_args_list[0][0][0]["action"] == "select_cell_by_index"
    assert mock_exec.call_args_list[1][0][0]["action"] == "click_cell_delete_button"


def test_delete_by_index_dispatches_without_waiting():
    with patch("testing.host.bot_command._send_msg") as mock_send:
        out = execute_bot_command_sync(
            {
                "action": "delete_by_index",
                "url": "https://example.com/edit",
                "cell_index": 3,
                "fire_and_forget": True,
            },
            timeout=0.1,
        )
    assert out["ok"] is True
    assert out["result"]["dispatched"] is True
    mock_send.assert_called_once()


def test_delete_by_index_waits_for_extension_by_default():
    with patch("testing.host.bot_command._send_msg") as mock_send:
        out = execute_bot_command_sync(
            {"action": "delete_by_index", "url": "https://example.com/edit", "cell_index": 3},
            timeout=0.1,
        )
    assert out["ok"] is False
    mock_send.assert_called_once()
