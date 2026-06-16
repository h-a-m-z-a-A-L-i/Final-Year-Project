#!/usr/bin/env python3
"""Live smoke test for delete_by_index.

Selects the cell (same mechanism as smoke_select_inside_cell.py), then clicks
the delete toolbar button that appears on the active cell.

Prerequisites:
  - host.py running
  - notebook /edit tab open in Chrome
  - extension loaded (reload after JS changes)

Usage (PowerShell — one line):
  python testing/host/scripts/smoke_delete_cell.py --cell-index 2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"

HOST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = HOST_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from testing.host.bot_command_client import is_native_host_process  # noqa: E402
from testing.host.tool_registry import registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test delete_by_index (select + delete button)")
    parser.add_argument("--url", default=DEFAULT_URL, help="Open notebook /edit URL")
    parser.add_argument("--cell-index", type=int, required=True, help="1-based cell label to delete")
    parser.add_argument("--tab-id", type=int, default=None)
    args = parser.parse_args()

    if is_native_host_process():
        print("Transport: native host (inside host.py)")
    else:
        print("Transport: bot_commands.jsonl (host.py must be running)")

    payload = {"url": args.url, "cell_index": args.cell_index}
    if args.tab_id is not None:
        payload["tab_id"] = args.tab_id

    print(f"Deleting cell {args.cell_index} on {args.url} (select then delete button)")
    result = registry().call("delete_by_index", payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
