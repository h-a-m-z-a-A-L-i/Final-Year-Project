import sys
import os
from pathlib import Path

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import tool_registry as tr
from testing.host import persistence_helpers as ph
from testing.host import config
import importlib


def test_insert_cell_inproc(tmp_path, monkeypatch):
    temp_scraped = tmp_path / 'notebooks'
    monkeypatch.setattr(config, 'SCRAPED_DIR', temp_scraped)

    url = 'https://example.com/notebook'
    filename = ph.get_safe_filename(url)
    persistent_dir = temp_scraped / 'persistent'
    persistent_dir.mkdir(parents=True, exist_ok=True)
    ppath = persistent_dir / filename

    initial = {"cells": [{"index": 1, "input": "print(1)"}, {"index": 2, "input": "print(2)"}]}
    ph._atomic_write_json(ppath, initial)

    importlib.reload(ph)
    importlib.reload(tr)

    tr.sync_persistence_for_action(
        "insert_cell",
        {"url": url, "index": 1, "direction": "below"},
        {"ok": True},
    )
    data = ph.read_json_file(ppath)
    assert isinstance(data, dict)
    assert len(data.get('cells', [])) == 3


def test_edit_and_delete_inproc(tmp_path, monkeypatch):
    temp_scraped = tmp_path / 'notebooks'
    monkeypatch.setattr(config, 'SCRAPED_DIR', temp_scraped)

    url = 'https://example.com/notebook'
    filename = ph.get_safe_filename(url)
    persistent_dir = temp_scraped / 'persistent'
    persistent_dir.mkdir(parents=True, exist_ok=True)
    ppath = persistent_dir / filename

    initial = {"cells": [{"index": 1, "input": "a"}, {"index": 2, "input": "b"}, {"index": 3, "input": "c"}]}
    ph._atomic_write_json(ppath, initial)

    importlib.reload(ph)
    importlib.reload(tr)

    tr.sync_persistence_for_action(
        "edit_cell_by_index",
        {"url": url, "cell_index": 2, "content": "updated"},
        {"ok": True},
    )
    data = ph.read_json_file(ppath)
    assert data['cells'][1]['input'] == 'updated'

    tr.sync_persistence_for_action(
        "delete_by_index",
        {"url": url, "cell_index": 1},
        {"ok": True},
    )
    data = ph.read_json_file(ppath)
    assert len(data['cells']) == 2
