import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import config
from testing.host import persistence_helpers as ph
from testing.host import local_notebook_tools as lnt
from testing.host.tool_registry import build_cerebras_tools, registry

FIXTURE = {
    "cells": [
        {"index": 1, "type": "code", "input": "import pandas as pd\nmodel_df = pd.DataFrame({'a': [1,2,3]})", "output": ""},
        {"index": 2, "type": "code", "input": "print(model_df.head())", "output": "   a\n0  1"},
        {"index": 3, "type": "markdown", "input": "# Notes", "output": ""},
    ]
}


def _setup(tmp_path, monkeypatch):
    url = "https://example.com/notebook/edit"
    scraped = tmp_path / "notebooks"
    live = scraped / "live"
    live.mkdir(parents=True)
    ph._atomic_write_json(live / ph.get_safe_filename(url), FIXTURE)
    monkeypatch.setattr(config, "SCRAPED_DIR", scraped)
    return url


def test_notebook_find_symbol(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    out = lnt.notebook_find_symbol({"url": url, "symbol": "model_df"})
    assert out["ok"] is True
    assert out["latest_definition_cell"] == 1
    assert out["recommended_insert_below"] == 1


def test_notebook_get_cell_and_search(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    cell = lnt.notebook_get_cell({"url": url, "cell_index": 2, "include_output": True})
    assert cell["ok"] is True
    assert "model_df" in cell["cell"]["input"]

    hits = lnt.notebook_search({"url": url, "query": "model_df"})
    assert hits["ok"] is True
    assert hits["hit_count"] >= 1


def test_notebook_graph_query_has_dependencies(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    graph = lnt.notebook_graph_query({"url": url})
    assert graph["ok"] is True
    assert len(graph["graph"]) >= 2


def test_cerebras_tools_local_only_excludes_browser():
    tools = build_cerebras_tools(local_only=True)
    names = {t["function"]["name"] for t in tools}
    assert "notebook_get_cell" in names
    assert "click_cell" not in names
    assert "insert_cell" not in names


def test_notebook_recommend_placement(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    out = lnt.notebook_recommend_placement({"url": url, "symbols": ["model_df"]})
    assert out["ok"] is True
    rec = out["recommendation"]
    assert rec["insert_below_cell_index"] == 1
    assert "insert_new_code_cell" in rec["action"]
    assert "Insert Code Cell Below" in rec["instruction"]


def test_registry_local_call(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    reg = registry()
    out = reg.call("notebook_list_cells", {"url": url})
    assert out["ok"] is True
    assert out["cell_count"] == 3
