#!/usr/bin/env python3
"""Unified smoke runner for isolated browser tools.

Usage:
  python testing/host/scripts/smoke_browser_tool.py --tool click_cell --url URL --cell-index 1
  python testing/host/scripts/smoke_browser_tool.py --tool insert_cell --url URL --index 2
  python testing/host/scripts/smoke_browser_tool.py --tool delete_by_index --url URL --cell-index 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = HOST_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from testing.host.bot_command_client import is_native_host_process  # noqa: E402
from testing.host.tool_registry import registry  # noqa: E402

BROWSER_TOOLS = (
    "click_cell",
    "select_cell_by_index",
    "insert_cell",
    "edit_cell_by_index",
    "insert_and_edit_cell",
    "run_cell",
    "edit_and_run_cell",
    "delete_by_index",
    "creating_markdown_by_index",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test any isolated browser tool")
    parser.add_argument("--tool", required=True, choices=BROWSER_TOOLS)
    parser.add_argument("--url", required=True)
    parser.add_argument("--cell-index", type=int, default=None, help="1-based cell label")
    parser.add_argument("--index", type=int, default=None, help="1-based anchor (insert/markdown)")
    parser.add_argument("--content", default=None, help="Cell source (edit tools)")
    parser.add_argument("--direction", default="below", choices=["below", "above"])
    parser.add_argument("--run-cell", action="store_true", help="For click_cell only")
    parser.add_argument("--tab-id", type=int, default=None)
    args = parser.parse_args()

    if is_native_host_process():
        print("Note: running inside host.py process.")
    else:
        print("Transport: bot_commands.jsonl queue (host.py must be running).")

    payload: dict = {"url": args.url}
    if args.tab_id is not None:
        payload["tab_id"] = args.tab_id

    tool = args.tool
    if tool in {
        "click_cell",
        "select_cell_by_index",
        "edit_cell_by_index",
        "insert_and_edit_cell",
        "run_cell",
        "edit_and_run_cell",
        "delete_by_index",
    }:
        if args.cell_index is None:
            print(f"--cell-index is required for {tool}", file=sys.stderr)
            return 2
        payload["cell_index"] = args.cell_index
    if tool in {"insert_cell", "creating_markdown_by_index"}:
        idx = args.index if args.index is not None else args.cell_index
        if idx is None:
            print(f"--index (or --cell-index) is required for {tool}", file=sys.stderr)
            return 2
        payload["index"] = idx
    if tool in {"edit_cell_by_index", "insert_and_edit_cell", "edit_and_run_cell"}:
        if not args.content:
            print(f"--content is required for {tool}", file=sys.stderr)
            return 2
        payload["content"] = args.content
    if tool == "insert_cell":
        payload["direction"] = args.direction
    if tool == "click_cell" and args.run_cell:
        payload["run_cell"] = True

    print(f"Calling {tool} with:", json.dumps(payload, indent=2))
    result = registry().call(tool, payload)
    print("\nResult:")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    reported_tool = result.get("tool")
    if reported_tool and reported_tool != tool:
        print(f"\nWARNING: response tool={reported_tool!r} does not match requested {tool!r}", file=sys.stderr)

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
