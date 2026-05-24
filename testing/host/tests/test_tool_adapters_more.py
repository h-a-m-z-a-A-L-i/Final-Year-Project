import sys
import os
import importlib
import uuid
from pathlib import Path
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import tool_adapters as ta
from testing.host import persistence_helpers as ph
from testing.host import config
from testing.host import tool_registry as tr
from testing.host import bot_command as bc


def test_insert_and_edit_inproc(tmp_path, monkeypatch):
    # configure SCRAPED_DIR
    temp_scraped = tmp_path / 'notebooks'
    monkeypatch.setattr(config, 'SCRAPED_DIR', temp_scraped)
    importlib.reload(ph)
    importlib.reload(tr)
    importlib.reload(ta)

    url = 'https://example.com/notebook'
    filename = ph.get_safe_filename(url)
    persistent_dir = temp_scraped / 'persistent'
    persistent_dir.mkdir(parents=True, exist_ok=True)
    ppath = persistent_dir / filename

    initial = {"cells": [{"index": 1, "input": "a"}]}
    ph._atomic_write_json(ppath, initial)

    def _fake_execute(cmd, timeout=12.0):
        action = str(cmd.get("action") or "").lower()
        if action == "insert_cell":
            tr.sync_persistence_for_action("insert_cell", cmd, {"ok": True})
            return {"ok": True, "result": {"ok": True, "cellIndex": 2}}
        if action in {"edit_cell_by_index", "edit_cell"}:
            tr.sync_persistence_for_action("edit_cell_by_index", cmd, {"ok": True})
            return {"ok": True, "result": {"ok": True, "cellIndex": cmd.get("cellIndex")}}
        return {"ok": True, "result": {"ok": True}}

    with patch.object(bc, "execute_bot_command_sync", side_effect=_fake_execute):
        res = ta.insert_and_edit_cell({'url': url, 'index': 1, 'direction': 'below', 'content': 'inserted content'})
    assert res.get('ok') is True

    data = ph.read_json_file(ppath)
    assert len(data.get('cells', [])) == 2
    # last cell should have content
    assert data['cells'][-1]['input'] == 'inserted content'


def test_convert_to_markdown_adapter(monkeypatch):
    # Ensure adapter forwards to registry
    calls = []

    class DummyReg:
        def call(self, name, args):
            calls.append((name, args))
            return {"ok": True}

    monkeypatch.setattr(tr, 'registry', lambda: DummyReg())
    importlib.reload(ta)

    res = ta.convert_cell_to_markdown_by_index({'index': 2, 'url': 'https://x'})
    assert res.get('ok') is True
    assert calls and calls[0][0] == 'creating_markdown_by_index'
