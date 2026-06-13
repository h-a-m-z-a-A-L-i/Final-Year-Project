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
