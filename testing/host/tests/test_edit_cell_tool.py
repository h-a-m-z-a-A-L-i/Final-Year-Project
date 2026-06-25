import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.edit_cell_tool import run_edit_cell
from testing.host import bot_tool_utils as btu


def test_normalize_edit_cell_args_maps_app_index_to_dom():
    cmd, err = btu.normalize_edit_cell_args(
        {"url": "https://example.com/edit", "cell_index": 3, "content": "print(1)"}
    )
    assert err is None
    assert cmd["dom_index"] == 2
    assert cmd["app_index"] == 3
    assert cmd["cellIndex"] == 2
    assert cmd["content"] == "print(1)"


def test_edit_cell_requires_content():
    out = run_edit_cell({"url": "https://example.com/edit", "cell_index": 1})
    assert out["ok"] is False
    assert "content" in out["error"].lower()


def test_edit_cell_success():
    with patch("testing.host.edit_cell_tool.dispatch_edit_cell") as mock_dispatch:
        mock_dispatch.return_value = {"ok": True, "result": {"ok": True, "dispatched": True}}
        out = run_edit_cell(
            {
                "url": "https://example.com/edit",
                "cell_index": 1,
                "content": "print(1)",
            }
        )

    assert out["ok"] is True
    assert out["cell_index"] == 1
    assert out["dom_index"] == 0
    assert out["dispatched"] is True
    assert out["phase"] == "dispatched"
    mock_dispatch.assert_called_once()
