#!/usr/bin/env python3
"""Live smoke test for edit_cell_by_index (Step 1).

Prerequisites:
  1. host.py running
  2. Kaggle notebook /edit tab open
  3. Extension reloaded after JS changes

Usage:
  python testing/host/scripts/smoke_edit_cell.py --url "https://www.kaggle.com/code/.../edit" --cell-index 1 --content "print('hello')"
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
    parser = argparse.ArgumentParser(description="Smoke test edit_cell_by_index tool")
    parser.add_argument("--url", default=DEFAULT_URL, help="Open notebook /edit URL")
    parser.add_argument("--cell-index", type=int, default=1, help="1-based cell label")
    parser.add_argument("--content", default="print('smoke_edit_cell')", help="Cell source to write")
    parser.add_argument("--tab-id", type=int, default=None)
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

    print("Calling edit_cell_by_index with:", json.dumps(payload, indent=2))
    result = registry().call("edit_cell_by_index", payload)
    print("\nResult:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
