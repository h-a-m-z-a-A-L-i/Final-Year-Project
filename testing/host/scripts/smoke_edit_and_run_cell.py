#!/usr/bin/env python3
"""Live smoke test for edit_and_run_cell (ReAct fix-and-run step).

Prerequisites:
  1. host.py running
  2. Kaggle notebook /edit tab open (kernel on)
  3. Extension reloaded

Usage:
  python testing/host/scripts/smoke_edit_and_run_cell.py \
    --url "https://www.kaggle.com/code/.../edit" \
    --cell-index 2 \
    --content "print('hello')"
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test edit_and_run_cell tool")
    parser.add_argument("--url", required=True, help="Open notebook /edit URL")
    parser.add_argument("--cell-index", type=int, required=True, help="1-based cell label")
    parser.add_argument("--content", required=True, help="New cell source to paste and run")
    parser.add_argument("--tab-id", type=int, default=None, help="Optional Chrome tab id")
    args = parser.parse_args()

    if is_native_host_process():
        print("Note: running inside host.py process (native transport).")
    else:
        print("Transport: bot_commands.jsonl queue (host.py must be running).")

    payload = {
        "url": args.url,
        "cell_index": args.cell_index,
        "content": args.content,
    }
    if args.tab_id is not None:
        payload["tab_id"] = args.tab_id

    print("Calling edit_and_run_cell with:", json.dumps(payload, indent=2))
    result = registry().call("edit_and_run_cell", payload)
    print("\nResult:")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if not result.get("ok"):
        return 1

    print(
        "\nEdit + run triggered. Snapshot refresh runs ~2s after execute; "
        "use notebook_get_cell locally to read output.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
