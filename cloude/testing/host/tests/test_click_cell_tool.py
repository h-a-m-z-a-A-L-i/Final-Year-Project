import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.click_cell_tool import run_click_cell


def test_click_cell_select_then_enter():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.side_effect = [
            {
                "ok": True,
                "result": {"ok": True, "domIndex": 4, "appIndex": 5, "dispatched": True},
            },
            {"ok": True, "result": {"ok": True, "key": "Enter", "dispatched": True}},
        ]
        with patch("testing.host.click_cell_tool.time.sleep"):
            out = run_click_cell(
                {
                    "url": "https://example.com/edit",
                    "cell_index": 5,
                }
            )

    assert out["ok"] is True
    assert out["tool"] == "click_cell"
    assert out["cell_index"] == 5
    assert out["strategy"] == "select-then-enter"
    assert mock_exec.call_count == 2
    assert mock_exec.call_args_list[0][0][0]["action"] == "select_cell_by_index"
    assert mock_exec.call_args_list[1][0][0]["action"] == "send_key"
    assert mock_exec.call_args_list[1][0][0]["key"] == "Enter"


def test_click_cell_fails_if_select_fails():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.return_value = {"ok": False, "error": "select failed"}
        out = run_click_cell({"url": "https://example.com/edit", "cell_index": 3})

    assert out["ok"] is False
    assert out["tool"] == "click_cell"
    mock_exec.assert_called_once()


def test_click_cell_rejects_tab_id_like_index():
    out = run_click_cell({"url": "https://example.com/edit", "cell_index": 2015855861})
    assert out["ok"] is False
    assert "tab id" in out["error"].lower()


def test_click_cell_accepts_first_labeled_cell():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.side_effect = [
            {"ok": True, "result": {"ok": True, "domIndex": 0, "appIndex": 1}},
            {"ok": True, "result": {"ok": True, "key": "Enter"}},
        ]
        with patch("testing.host.click_cell_tool.time.sleep"):
            out = run_click_cell({"url": "https://example.com/edit", "cell_index": 1})
    assert out["ok"] is True
    assert out["cell_index"] == 1
    assert mock_exec.call_count == 2


def test_click_cell_no_call_on_validation_error():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        out = run_click_cell({"url": "https://example.com/edit", "cell_index": -1})
    assert out["ok"] is False
    mock_exec.assert_not_called()
