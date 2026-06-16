import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import bot_command as bc


def test_run_insert_code_below_flow_composes_insert_and_set():
    with patch.object(
        bc,
        "run_insert_cell_flow",
        return_value={"ok": True, "result": {"ok": True, "cellIndex": 4}},
    ), patch.object(
        bc,
        "run_set_cell_content_flow",
        return_value={"ok": True, "result": {"ok": True, "cellIndex": 4, "chars": 7}},
    ):
        event = bc.run_insert_code_below_flow(
            {
                "action": "insert_code_below",
                "url": "https://example.com/edit",
                "index": 3,
                "content": "print(1)",
                "tabId": 1,
            },
            timeout=5,
        )

    assert event.get("ok") is True
    assert event.get("result", {}).get("newDomIndex") == 4
    assert event.get("result", {}).get("insertedBelow") == 2


def test_insert_code_below_reparents_inner_click_failure_request_id():
    import uuid
    from unittest.mock import patch

    outer_id = str(uuid.uuid4())
    inner_id = str(uuid.uuid4())

    with patch.object(
        bc,
        "run_insert_cell_flow",
        return_value={
            "ok": False,
            "type": "CLICK",
            "requestId": inner_id,
            "result": {"ok": False, "error": "timeout waiting for extension (is the notebook tab open?)"},
            "error": "timeout",
        },
    ):
        event = bc.run_insert_code_below_flow(
            {
                "action": "insert_and_edit_cell",
                "requestId": outer_id,
                "url": "https://example.com/edit",
                "index": 2,
                "content": "print(1)",
            },
            timeout=8,
        )

    assert event.get("requestId") == outer_id
    assert event.get("ok") is False
    assert event.get("result", {}).get("phase") == "insert_failed"
