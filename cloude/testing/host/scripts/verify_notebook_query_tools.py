#!/usr/bin/env python3
"""Verify every local notebook query tool against live or fixture snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import config
from testing.host import persistence_helpers as ph
from testing.host.local_notebook_tools import LOCAL_TOOL_NAMES
from testing.host.notebook_query import build_query_plan, execute_query_plan, prefetch_notebook_queries
from testing.host.tool_registry import registry


def _fixture_url(tmp: str) -> str:
    url = "https://example.com/verify-notebook-tools/edit"
    live = os.path.join(tmp, "live")
    os.makedirs(live, exist_ok=True)
    data = {
        "cells": [
            {"index": 1, "type": "markdown", "input": "# Dataset notes", "output": ""},
            {
                "index": 2,
                "type": "code",
                "input": "import pandas as pd\nmodel_df = pd.read_csv('data.csv')",
                "output": "ok",
            },
        ]
    }
    path = Path(live) / ph.get_safe_filename(url)
    ph._atomic_write_json(path, data)
    config.SCRAPED_DIR = Path(tmp)
    return url


def _default_args(tool: str, url: str) -> dict:
    base = {"url": url}
    if tool == "notebook_get_cell":
        return {**base, "cell_index": 2}
    if tool == "notebook_get_cells":
        return {**base, "cell_indices": [1, 2]}
    if tool == "notebook_find_symbol":
        return {**base, "symbol": "model_df"}
    if tool == "notebook_search":
        return {**base, "query": "read_csv"}
    if tool == "notebook_cell_neighbors":
        return {**base, "cell_index": 2}
    if tool == "notebook_recommend_placement":
        return {**base, "symbols": ["model_df"]}
    if tool == "notebook_overview":
        return {**base, "search_terms": ["read_csv"]}
    if tool == "notebook_executed_cells":
        return base
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify notebook query tools")
    parser.add_argument("--url", help="Notebook URL (live snapshot). Uses fixture if omitted.")
    parser.add_argument("--mode", default="ask", choices=["ask", "code", "agentic"])
    parser.add_argument("--prompt", default="what is the dataset about?")
    args = parser.parse_args()

    if args.url:
        url = args.url.strip()
    else:
        import tempfile

        url = _fixture_url(tempfile.mkdtemp())

    reg = registry()
    print(f"URL: {url}\n=== Per-tool smoke ===")
    failed = 0
    for name in sorted(LOCAL_TOOL_NAMES):
        payload = reg.call(name, _default_args(name, url))
        ok = isinstance(payload, dict) and payload.get("ok")
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if not ok:
            failed += 1
            print(f"         {payload.get('error', payload)}")

    print(f"\n=== Mode query plan ({args.mode}) ===")
    plan = build_query_plan(
        mode=args.mode,
        prompt=args.prompt,
        url=url,
        static_cache=True,
        agentic=args.mode == "agentic",
    )
    for step in plan:
        print(f"  - {step.tool}: {step.reason}")
    results = execute_query_plan(reg, plan)
    for row in results:
        print(f"  [{ 'OK' if row.ok else 'FAIL' }] {row.tool}")

    print("\n=== Prefetch block (first 500 chars) ===")
    block, _ = prefetch_notebook_queries(
        registry=reg,
        mode=args.mode,
        prompt=args.prompt,
        url=url,
        static_cache=True,
        agentic=args.mode == "agentic",
    )
    print(block[:500] + ("..." if len(block) > 500 else ""))

    if failed:
        print(f"\n{failed} tool(s) failed.")
        return 1
    print("\nAll tools passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
