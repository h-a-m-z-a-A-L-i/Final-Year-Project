import json
import os
import sys
import threading

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import config
from testing.host.notebook_data_handler import handle_notebook_data
from testing.host.persistence_helpers import get_safe_filename, read_json_file
from testing.host.snapshot_verification import evaluate_persistent_update


def _ctx(logs, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCRAPED_DIR", tmp_path / "notebooks")
    monkeypatch.setattr(config, "HASHES_PATH", tmp_path / "meta" / "hashes.json")
    monkeypatch.setattr(config, "EXECUTION_STATE_PATH", tmp_path / "meta" / "execution_state.json")
    (tmp_path / "meta").mkdir(parents=True, exist_ok=True)

    return {
        "dep_manager": type("DM", (), {"get_builder": lambda self, u: None})(),
        "send_msg": lambda _msg: None,
        "log": logs.append,
        "bot_state": {"tabId": None, "url": None},
        "bot_state_lock": threading.Lock(),
    }


def test_repeated_identical_scrape_does_not_rewrite_persistent(tmp_path, monkeypatch):
    logs = []
    ctx = _ctx(logs, tmp_path, monkeypatch)
    url = "https://www.kaggle.com/code/test/persistent-guard/edit"
    msg = {
        "type": "NOTEBOOK_DATA",
        "tabUrl": url,
        "tabId": 42,
        "title": "Guard Notebook",
        "kernelStatus": "running",
        "kernelScenario": "scenario_3_reload_running_kernel",
        "kernelState": {},
        "cells": [
            {
                "type": "code",
                "index": 1,
                "source": 'print("hello")',
                "output": "hello",
                "execution_order": 1,
                "execution_title": "Execution #1",
                "execution_status": "executed",
            }
        ],
    }

    handle_notebook_data(ctx, msg)
    ppath = config.SCRAPED_DIR / "persistent" / get_safe_filename(url)
    assert ppath.is_file()
    first = read_json_file(ppath)

    handle_notebook_data(ctx, msg)
    second = read_json_file(ppath)

    decision = evaluate_persistent_update(first, second)
    assert decision.allow_write is False
    assert decision.reason == "unchanged"
    assert first.get("cells")[0]["input"] == 'print("hello")'


def test_kernel_scrape_syncs_persistent_when_live_has_fewer_cells(tmp_path, monkeypatch):
    logs = []
    ctx = _ctx(logs, tmp_path, monkeypatch)
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    storage_key = "kaggle:kernel:112732919"
    registry_path = tmp_path / "meta" / "notebook_registry.json"
    registry_path.write_text(
        json.dumps({"url_to_key": {url: storage_key}, "kernels": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("testing.host.notebook_identity.NOTEBOOK_REGISTRY_PATH", registry_path)

    stale_persistent = {
        "tabUrl": url,
        "title": "Stale",
        "lastUpdated": "2020-01-01",
        "notebookId": 112732919,
        "storageKey": storage_key,
        "cells": [
            {"type": "code", "index": 1, "input": "old_a", "output": "1"},
            {"type": "code", "index": 2, "input": "old_b", "output": "2"},
        ],
    }
    ppath = config.SCRAPED_DIR / "persistent" / "kaggle_kernel_112732919.json"
    ppath.parent.mkdir(parents=True, exist_ok=True)
    ppath.write_text(json.dumps(stale_persistent), encoding="utf-8")

    msg = {
        "type": "NOTEBOOK_DATA",
        "tabUrl": url,
        "tabId": 7,
        "notebookId": 112732919,
        "title": "Fresh",
        "kernelStatus": "idle",
        "kernelScenario": "unknown",
        "kernelState": {},
        "cells": [
            {
                "type": "code",
                "index": 1,
                "source": 'print("new")',
                "output": "new",
            }
        ],
    }

    handle_notebook_data(ctx, msg)

    live_path = config.SCRAPED_DIR / "live" / "kaggle_kernel_112732919.json"
    assert live_path.is_file()
    live = read_json_file(live_path)
    persistent = read_json_file(ppath)

    assert len(live["cells"]) == 1
    assert live["cells"][0]["input"] == 'print("new")'
    assert persistent == live
    assert any("persistent" in line.lower() and "updated" in line.lower() for line in logs)


def test_sync_from_live_allows_partial_scrape_update():
    existing = {
        "tabUrl": "https://example.com/code/u/n/edit",
        "cells": [
            {"index": 1, "input": "a"},
            {"index": 2, "input": "b"},
        ],
    }
    incoming = {"tabUrl": "https://example.com/code/u/n/edit", "cells": [{"index": 1, "input": "a"}]}
    blocked = evaluate_persistent_update(existing, incoming)
    assert blocked.allow_write is False
    allowed = evaluate_persistent_update(existing, incoming, sync_from_live=True)
    assert allowed.allow_write is True
    assert allowed.reason == "content_changed"
