#!/usr/bin/env python3
"""Independent cell selection by 1-based index (insert anchor path).

Select only — activates Kaggle cell chrome (toolbar / delete / run) then presses
Escape so you are NOT left in code edit mode.

Prerequisites:
  - host.py running
  - notebook /edit tab open in Chrome
  - extension loaded (reload after JS changes)

Usage (PowerShell — one line):
  python testing/host/scripts/smoke_select_inside_cell.py --url "https://www.kaggle.com/code/codekey/testing-ol/edit" --cell-index 1

PowerShell multi-line (use backtick, not ^):
  python testing/host/scripts/smoke_select_inside_cell.py `
    --url "https://www.kaggle.com/code/codekey/testing-ol/edit" `
    --cell-index 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from testing.host.bot_command_client import execute_bot_command, is_native_host_process  # noqa: E402


def select_cell_by_index(
    *,
    url: str,
    cell_index: int,
    tab_id: int | None = None,
    timeout: float = 8.0,
) -> dict:
    """Select cell `cell_index` (1-based label) without running it."""
    cmd = {
        "action": "select_cell_by_index",
        "url": url,
        "cell_index": cell_index,
    }
    if tab_id is not None:
        cmd["tab_id"] = tab_id
    return execute_bot_command(cmd, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independent cell selection by 1-based index (insert anchor path)",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Open notebook /edit URL")
    parser.add_argument(
        "--cell-index",
        type=int,
        required=True,
        help="1-based cell label (first cell = 1)",
    )
    parser.add_argument("--tab-id", type=int, default=None)
    args = parser.parse_args()

    if is_native_host_process():
        print("Transport: native host (inside host.py)")
    else:
        print("Transport: bot_commands.jsonl (host.py must be running)")

    print(f"Selecting cell {args.cell_index} on {args.url}")
    result = select_cell_by_index(
        url=args.url,
        cell_index=args.cell_index,
        tab_id=args.tab_id,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
