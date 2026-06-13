import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.click_cell_tool import run_click_cell


def test_click_cell_succeeds_on_first_attempt():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.return_value = {
            "ok": True,
            "result": {"ok": True, "domIndex": 4, "appIndex": 5, "strategy": "dom-click-only"},
        }
        out = run_click_cell(
            {
                "url": "https://example.com/edit",
                "cell_index": 5,
            }
        )

    assert out["ok"] is True
    assert out["tool"] == "click_cell"
    assert out["cell_index"] == 5
    assert out["dom_index"] == 4
    mock_exec.assert_called_once()
    sent = mock_exec.call_args[0][0]
    assert sent["cellIndex"] == 4
    assert sent["dom_index"] == 4


def test_click_cell_fails_without_retry():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.return_value = {"ok": False, "error": "Cell not found in this frame tree."}
        out = run_click_cell({"url": "https://example.com/edit", "cell_index": 3})

    assert out["ok"] is False
    assert out["tool"] == "click_cell"
    assert out["cell_index"] == 3
    mock_exec.assert_called_once()


def test_click_cell_rejects_tab_id_like_index():
    out = run_click_cell({"url": "https://example.com/edit", "cell_index": 2015855861})
    assert out["ok"] is False
    assert "tab id" in out["error"].lower()


def test_click_cell_accepts_first_labeled_cell():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.return_value = {"ok": True, "result": {"ok": True, "domIndex": 0, "appIndex": 1}}
        out = run_click_cell({"url": "https://example.com/edit", "cell_index": 1})
    assert out["ok"] is True
    assert out["cell_index"] == 1
    assert out["dom_index"] == 0
    mock_exec.assert_called_once()


def test_click_cell_dom_basis_still_supported():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.return_value = {"ok": True, "result": {"ok": True, "domIndex": 0, "appIndex": 1}}
        out = run_click_cell(
            {"url": "https://example.com/edit", "cell_index": 0, "index_basis": "dom"}
        )
    assert out["ok"] is True
    mock_exec.assert_called_once()
    assert mock_exec.call_args[0][0]["cellIndex"] == 0


def test_click_cell_no_call_on_validation_error():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        out = run_click_cell({"url": "https://example.com/edit", "cell_index": -1})
    assert out["ok"] is False
    mock_exec.assert_not_called()
