import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.insert_and_edit_cell_tool import run_insert_and_edit_cell
from testing.host import bot_tool_utils as btu


def test_normalize_insert_and_edit_args_maps_app_index_to_dom():
    cmd, err = btu.normalize_insert_and_edit_args(
        {
            "url": "https://example.com/edit",
            "cell_index": 3,
            "content": "print(1)",
        }
    )
    assert err is None
    assert cmd["dom_index"] == 2
    assert cmd["app_index"] == 3
    assert cmd["action"] == "insert_and_edit_cell"
    assert cmd["direction"] == "below"
    assert cmd["content"] == "print(1)"


def test_insert_and_edit_requires_content():
    out = run_insert_and_edit_cell({"url": "https://example.com/edit", "cell_index": 1})
    assert out["ok"] is False
    assert "content" in out["error"].lower()


def test_insert_and_edit_success():
    with patch("testing.host.bot_command_client.execute_bot_command") as mock_exec:
        mock_exec.return_value = {
            "ok": True,
            "result": {
                "ok": True,
                "phase": "insert_code_below_complete",
                "insertedBelow": 2,
                "newDomIndex": 3,
                "chars": 8,
            },
        }
        out = run_insert_and_edit_cell(
            {
                "url": "https://example.com/edit",
                "cell_index": 3,
                "content": "print(1)",
            }
        )

    assert out["ok"] is True
    assert out["anchor_cell_index"] == 3
    assert out["new_cell_index"] == 4
    assert out["new_dom_index"] == 3
    mock_exec.assert_called_once()
    sent = mock_exec.call_args[0][0]
    assert sent["action"] == "insert_and_edit_cell"
    assert sent["dom_index"] == 2
