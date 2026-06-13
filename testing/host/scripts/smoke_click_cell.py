#!/usr/bin/env python3
"""Live smoke test for click_cell (Step 0).

Prerequisites:
  1. host.py running (native messaging to Chrome extension)
  2. Kaggle notebook /edit tab open in Chrome
  3. Extension loaded — reload after code changes

Uses bot_commands.jsonl queue (works from a normal terminal; does NOT require
running this script inside host.py).

Usage:
  python testing/host/scripts/smoke_click_cell.py --url "https://www.kaggle.com/code/.../edit" --cell-index 1
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


def _tail_bot_result(path: Path) -> dict | None:
    if not path.is_file():
        return None
    last = None
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test click_cell tool")
    parser.add_argument("--url", required=True, help="Open notebook /edit URL")
    parser.add_argument(
        "--cell-index",
        type=int,
        default=1,
        help="1-based cell label (first cell is 1; matches notebook JSON index)",
    )
    parser.add_argument("--run-cell", action="store_true", help="Run cell after click")
    parser.add_argument("--tab-id", type=int, default=None, help="Optional Chrome tab id")
    args = parser.parse_args()

    if is_native_host_process():
        print("Note: running inside host.py process (native transport).")
    else:
        print("Transport: bot_commands.jsonl queue (host.py must be running).")

    payload = {
        "url": args.url,
        "cell_index": args.cell_index,
        "run_cell": args.run_cell,
    }
    if args.tab_id is not None:
        payload["tab_id"] = args.tab_id

    print("Calling click_cell with:", json.dumps(payload, indent=2))
    result = registry().call("click_cell", payload)
    print("\nResult:")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    bot_results = HOST_DIR / "data" / "meta" / "bot_results.jsonl"
    tail = _tail_bot_result(bot_results)
    if tail:
        print("\nLast bot_results.jsonl entry type:", tail.get("type"), "ok=", tail.get("ok"))

    if not result.get("ok"):
        err = str(result.get("error") or "")
        if "host.py" in err or "timeout" in err.lower():
            print(
                "\nChecklist:\n"
                "  - Start host: python testing/host/host.py\n"
                "  - Open the same notebook URL in Chrome\n"
                "  - Reload the extension\n"
                f"  - Use 1-based cell label (you passed {args.cell_index}; first cell is 1)",
                file=sys.stderr,
            )

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
