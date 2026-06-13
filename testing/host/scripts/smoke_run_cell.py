#!/usr/bin/env python3
"""Live smoke test for run_cell (Step 3).

Prerequisites:
  1. host.py running (native messaging to Chrome extension)
  2. Kaggle notebook /edit tab open in Chrome (kernel on for execution)
  3. Extension loaded — reload after code changes

Usage:
  python testing/host/scripts/smoke_run_cell.py --url "https://www.kaggle.com/code/.../edit" --cell-index 2
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
    parser = argparse.ArgumentParser(description="Smoke test run_cell tool")
    parser.add_argument("--url", required=True, help="Open notebook /edit URL")
    parser.add_argument(
        "--cell-index",
        type=int,
        default=1,
        help="1-based cell label to execute (first cell is 1)",
    )
    parser.add_argument("--tab-id", type=int, default=None, help="Optional Chrome tab id")
    args = parser.parse_args()

    if is_native_host_process():
        print("Note: running inside host.py process (native transport).")
    else:
        print("Transport: bot_commands.jsonl queue (host.py must be running).")

    payload = {
        "url": args.url,
        "cell_index": args.cell_index,
    }
    if args.tab_id is not None:
        payload["tab_id"] = args.tab_id

    print("Calling run_cell with:", json.dumps(payload, indent=2))
    result = registry().call("run_cell", payload)
    print("\nResult:")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if not result.get("ok"):
        err = str(result.get("error") or "")
        if "host.py" in err or "timeout" in err.lower():
            print(
                "\nChecklist:\n"
                "  - Start host: python testing/host/host.py\n"
                "  - Open the same notebook URL in Chrome\n"
                "  - Reload the extension\n"
                "  - Turn the kernel on if it is off\n"
                f"  - Use 1-based cell label (you passed {args.cell_index}; first cell is 1)",
                file=sys.stderr,
            )
        return 1

    print(
        "\nRun triggered. Snapshot refresh is scheduled ~2s after success "
        "(extension sendTabs); wait a few seconds for output in live JSON.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
