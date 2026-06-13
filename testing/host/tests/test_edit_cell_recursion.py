import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import bot_command as bc


def test_set_cell_content_flow_does_not_recurse():
    calls = []

    def fake_browser(cmd, timeout=12.0):
        calls.append(cmd.get("action"))
        return {
            "ok": True,
            "result": {
                "ok": True,
                "strategy": "codemirror6-dispatch",
                "chars": 8,
                "domIndex": 6,
            },
        }

    with patch.object(bc, "_execute_browser_command", side_effect=fake_browser) as mock_browser:
        with patch("testing.host.tool_registry.sync_persistence_for_action"):
            out = bc.run_set_cell_content_flow(
                {
                    "action": "set_cell_content",
                    "requestId": "req-1",
                    "url": "https://example.com/edit",
                    "cellIndex": 6,
                    "dom_index": 6,
                    "index_basis": "dom",
                    "content": "print(1)",
                },
                timeout=5,
            )

    assert out.get("ok") is True
    assert calls == ["set_cell_content"]
    assert mock_browser.call_args[0][0]["requestId"] == "req-1"


def test_set_cell_content_failure_keeps_outer_request_id():
    with patch.object(bc, "_execute_browser_command") as mock_browser:
        mock_browser.return_value = {
            "ok": False,
            "requestId": "inner-should-not-leak",
            "error": "timeout waiting for extension (is the notebook tab open?)",
            "result": {"ok": False, "error": "timeout waiting for extension (is the notebook tab open?)"},
        }
        out = bc.run_set_cell_content_flow(
            {
                "action": "set_cell_content",
                "requestId": "outer-req",
                "url": "https://example.com/edit",
                "cellIndex": 1,
                "dom_index": 1,
                "index_basis": "dom",
                "content": "x=1",
            },
            timeout=5,
        )

    assert out.get("ok") is False
    assert out.get("requestId") == "outer-req"
    assert mock_browser.call_args[0][0]["requestId"] == "outer-req"


def test_edit_cell_flow_uses_set_cell_content_action():
    captured = {}

    def fake_set_flow(cmd, timeout=12.0):
        captured["action"] = cmd.get("action")
        captured["dom_index"] = cmd.get("dom_index")
        return {"ok": True, "result": {"ok": True, "phase": "content_set", "domIndex": 6, "appIndex": 7}}

    with patch.object(bc, "run_set_cell_content_flow", side_effect=fake_set_flow) as mock_set:
        out = bc.run_edit_cell_flow(
            {
                "action": "edit_cell_by_index",
                "requestId": "req-2",
                "url": "https://example.com/edit",
                "cell_index": 7,
                "content": "x=1",
            },
            timeout=5,
        )

    assert out.get("ok") is True
    mock_set.assert_called_once()
    assert captured["action"] == "set_cell_content"
    assert captured["dom_index"] == 6
