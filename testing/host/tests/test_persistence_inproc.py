import sys
import os
from pathlib import Path
import json

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import tool_registry as tr
from testing.host import persistence_helpers as ph
from testing.host import config
import importlib


def test_insert_cell_inproc(tmp_path, monkeypatch):
    # Set SCRAPED_DIR to a temp directory
    temp_scraped = tmp_path / 'notebooks'
    monkeypatch.setattr(config, 'SCRAPED_DIR', temp_scraped)

    # Prepare a sample persistent notebook for a fake URL
    url = 'https://example.com/notebook'
    filename = ph.get_safe_filename(url)
    persistent_dir = temp_scraped / 'persistent'
    persistent_dir.mkdir(parents=True, exist_ok=True)
    ppath = persistent_dir / filename

    initial = {"cells": [{"index": 1, "input": "print(1)"}, {"index": 2, "input": "print(2)"}]}
    ph._atomic_write_json(ppath, initial)

    # Reload persistence_helpers and tool_registry so they pick up the patched SCRAPED_DIR
    importlib.reload(ph)
    importlib.reload(tr)

    # Call the in-process insert_cell implementation via registry
    entry = tr.registry().get('insert_cell')
    assert entry is not None
    func = entry['func']

    res = func({'url': url, 'index': 1, 'direction': 'below'})
    assert res.get('ok') is True
    # Read back file and ensure cell count increased
    data = ph.read_json_file(ppath)
    assert isinstance(data, dict)
    cells = data.get('cells', [])
    assert len(cells) == 3


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
    edit_entry = tr.registry().get('edit_cell_by_index')
    del_entry = tr.registry().get('delete_by_index')
    assert edit_entry and del_entry

    edit_res = edit_entry['func']({'url': url, 'cell_index': 2, 'content': 'updated'})
    assert edit_res.get('ok') is True
    data = ph.read_json_file(ppath)
    assert data['cells'][1]['input'] == 'updated'

    del_res = del_entry['func']({'url': url, 'cell_index': 1})
    assert del_res.get('ok') is True
    data = ph.read_json_file(ppath)
    assert len(data['cells']) == 2