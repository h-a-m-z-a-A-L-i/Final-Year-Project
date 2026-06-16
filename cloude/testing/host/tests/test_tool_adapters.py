import importlib
import sys
import os

# Ensure repo root is on sys.path for tests run via pytest
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import testing.host.tool_registry as tr
import testing.host.tool_adapters as ta


def test_adapters_call_registry(monkeypatch):
    # Replace registry() with a dummy that records calls
    class DummyRegistry:
        def __init__(self):
            self.calls = []

        def call(self, name, args, timeout=8.0):
            self.calls.append((name, args))
            return {"ok": True, "name": name, "args": args}

    dummy = DummyRegistry()
    monkeypatch.setattr(tr, "registry", lambda: dummy)

    # reload adapters to ensure they use the patched registry if needed
    importlib.reload(ta)

    res = ta.click_cell({"cell_index": 10})
    assert res["ok"] and res["name"] == "click_cell"

    res2 = ta.insert_cell({"index": 2, "direction": "below"})
    assert res2["ok"] and res2["name"] == "insert_cell"

    # verify dummy recorded calls
    assert ("click_cell", {"cell_index": 10}) in dummy.calls
    assert ("insert_cell", {"index": 2, "direction": "below"}) in dummy.calls
