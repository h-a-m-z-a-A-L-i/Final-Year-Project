import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import notebook_storage as ns
from testing.host.persistence_helpers import _atomic_write_json


def test_notebook_filename_uses_kernel_id():
    assert ns.notebook_filename("kaggle:kernel:112732919") == "kaggle_kernel_112732919.json"
    assert ns.notebook_filename("https://example.com/nb").endswith(".json")


def test_consolidate_kernel_notebook_merges_aliases(tmp_path, monkeypatch):
    from testing.host import config as host_config

    monkeypatch.setattr(host_config, "SCRAPED_DIR", tmp_path)
    url_a = "https://www.kaggle.com/code/alice/old-slug/edit"
    url_b = "https://www.kaggle.com/code/alice/new-slug/edit"
    storage_key = "kaggle:kernel:4242"
    payload_a = {
        "tabUrl": url_a,
        "cells": [{"type": "code", "index": 1, "input": "x=1", "output": "1", "execution_order": 5}],
        "lastUpdated": "2020-01-01",
    }
    payload_b = {
        "tabUrl": url_b,
        "cells": [
            {"type": "code", "index": 1, "input": "x=2", "output": "2"},
            {"type": "code", "index": 2, "input": "y=1", "output": "1"},
        ],
        "lastUpdated": "2026-01-01",
    }
    from testing.host.persistence_helpers import get_safe_filename

    _atomic_write_json(tmp_path / "live" / get_safe_filename(url_a), payload_a)
    _atomic_write_json(tmp_path / "persistent" / get_safe_filename(url_b), payload_b)

    assert ns.consolidate_kernel_notebook(storage_key, [url_a, url_b], preferred_tab_url=url_b)

    canon = tmp_path / "persistent" / "kaggle_kernel_4242.json"
    assert canon.is_file()
    data = json.loads(canon.read_text(encoding="utf-8"))
    assert data["notebookId"] == 4242
    assert data["storageKey"] == storage_key
    assert len(data["cells"]) == 2
    assert "execution_order" not in data["cells"][0]
    assert not (tmp_path / "live" / get_safe_filename(url_a)).is_file()
