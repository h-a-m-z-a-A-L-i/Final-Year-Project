"""Tests for mode-aware notebook query planning and execution."""

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import config
from testing.host import persistence_helpers as ph
from testing.host import local_notebook_tools as lnt
from testing.host.notebook_query import (
    build_query_plan,
    execute_query_plan,
    format_query_results_block,
    prefetch_notebook_queries,
    tools_allowed_for_mode,
)
from testing.host.tool_registry import registry

FIXTURE = {
    "cells": [
        {
            "index": 1,
            "type": "markdown",
            "input": "# Housing dataset\nScraped Zameen property listings for Lahore.",
            "output": "",
        },
        {
            "index": 2,
            "type": "code",
            "input": "import pandas as pd\nmodel_df = pd.read_csv('/kaggle/input/zameen.csv')",
            "output": "   price  city\n0  100  Lahore",
        },
        {
            "index": 3,
            "type": "code",
            "input": "print(model_df.head())",
            "output": "",
        },
    ]
}


def _setup(tmp_path, monkeypatch):
    url = "https://example.com/notebook/edit"
    live = tmp_path / "notebooks" / "live"
    live.mkdir(parents=True)
    ph._atomic_write_json(live / ph.get_safe_filename(url), FIXTURE)
    monkeypatch.setattr(config, "SCRAPED_DIR", tmp_path / "notebooks")
    return url


def test_notebook_overview_finds_markdown_and_load(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    out = lnt.notebook_overview({"url": url, "search_terms": ["read_csv"]})
    assert out["ok"] is True
    assert out["summary"]["markdown_cells"] >= 1
    assert out["summary"]["data_load_cells"] >= 1
    assert any("Housing" in m.get("text", "") for m in out["markdown_cells"])


def test_build_query_plan_dataset_ask_includes_overview():
    plan = build_query_plan(
        mode="ask",
        prompt="what is the dataset about?",
        url="https://example.com/nb/edit",
        static_cache=True,
    )
    tools = {s.tool for s in plan}
    assert "notebook_overview" in tools
    assert "notebook_executed_cells" in tools


def test_notebook_executed_cells_includes_head_output(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    FIXTURE2 = {
        "cells": [
            {
                "index": 1,
                "type": "code",
                "input": "import pandas as pd\ndf = pd.read_csv('data.csv')",
                "output": "",
            },
            {
                "index": 2,
                "type": "code",
                "input": "print(df.head())",
                "output": "   price  city  beds\n0  100  Lahore  3",
            },
        ]
    }
    live = tmp_path / "notebooks" / "live"
    ph._atomic_write_json(live / ph.get_safe_filename(url), FIXTURE2)
    out = lnt.notebook_executed_cells({"url": url})
    assert out["ok"] is True
    assert out["cell_count"] == 1
    assert out["cells"][0]["index"] == 2
    assert "price" in out["cells"][0]["output"]
    assert "print(df.head())" in out["cells"][0]["input"]


def test_build_query_plan_agentic_prefetches_target_cell():
    plan = build_query_plan(
        mode="agentic",
        prompt="run cell 2",
        url="u",
        cell_index=2,
        agentic=True,
    )
    tools = [s.tool for s in plan]
    assert "notebook_get_cell" in tools
    assert "notebook_cell_neighbors" in tools


def test_execute_all_local_tools(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    reg = registry()
    failures = []
    cases = {
        "notebook_snapshot_status": {"url": url},
        "notebook_list_cells": {"url": url},
        "notebook_graph_query": {"url": url},
        "notebook_get_cell": {"url": url, "cell_index": 2},
        "notebook_get_cells": {"url": url, "cell_indices": [1, 2]},
        "notebook_find_symbol": {"url": url, "symbol": "model_df"},
        "notebook_search": {"url": url, "query": "read_csv"},
        "notebook_cell_neighbors": {"url": url, "cell_index": 3},
        "notebook_recommend_placement": {"url": url, "symbols": ["model_df"]},
        "notebook_overview": {"url": url, "search_terms": ["dataset"]},
        "notebook_executed_cells": {"url": url},
    }
    for name, args in cases.items():
        out = reg.call(name, args)
        if not (isinstance(out, dict) and out.get("ok")):
            failures.append((name, out))
    assert not failures, failures


def test_prefetch_notebook_queries_block(tmp_path, monkeypatch):
    url = _setup(tmp_path, monkeypatch)
    reg = registry()
    block, results = prefetch_notebook_queries(
        registry=reg,
        mode="ask",
        prompt="what is dataset about?",
        url=url,
        static_cache=True,
    )
    assert results
    assert all(r.ok for r in results)
    assert "Notebook query results" in block
    assert "notebook_overview" in block


def test_tools_allowed_ask_vs_code():
    ask_tools = tools_allowed_for_mode("ask", agentic=False)
    assert "notebook_overview" in ask_tools
    code_tools = tools_allowed_for_mode("code", agentic=False)
    assert "notebook_graph_query" in code_tools


def test_format_query_results_block():
    from testing.host.notebook_query import QueryResult

    text = format_query_results_block(
        [QueryResult(tool="notebook_search", reason="test", payload={"ok": True, "hits": []}, ok=True)]
    )
    assert "notebook_search" in text
