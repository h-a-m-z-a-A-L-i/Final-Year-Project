#!/usr/bin/env python3
"""Live smoke: host batch executor (insert → edit → run) without LLM.

Prerequisites:
  - host.py running
  - notebook /edit tab open
  - extension reloaded
  - kernel on (for run verification)

Usage:
  python testing/host/scripts/smoke_agentic_batch.py --anchor-index 1 --content "print('batch_smoke')"
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

from testing.host.agentic_batch_executor import execute_agentic_batch  # noqa: E402
from testing.host.agentic_mode import browser_tool_allowed  # noqa: E402
from testing.host.tool_registry import registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke host agentic batch executor")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--anchor-index", type=int, default=1, help="1-based anchor for insert below")
    parser.add_argument("--content", default="print('batch_smoke')")
    parser.add_argument("--run", action="store_true", default=True)
    parser.add_argument("--no-run", action="store_false", dest="run")
    parser.add_argument("--tab-id", type=int, default=None)
    args = parser.parse_args()

    new_index = args.anchor_index + 1
    tool_calls = [
        {
            "id": "smoke_insert",
            "function": {
                "name": "insert_cell",
                "arguments": json.dumps(
                    {
                        "index": args.anchor_index,
                        "direction": "below",
                        "url": args.url,
                    }
                ),
            },
        },
        {
            "id": "smoke_edit",
            "function": {
                "name": "edit_cell_by_index",
                "arguments": json.dumps(
                    {
                        "cell_index": new_index,
                        "content": args.content,
                        "url": args.url,
                    }
                ),
            },
        },
    ]
    if args.run:
        tool_calls.append(
            {
                "id": "smoke_run",
                "function": {
                    "name": "run_cell",
                    "arguments": json.dumps(
                        {"cell_index": new_index, "url": args.url}
                    ),
                },
            }
        )

    prompt = f"insert below cell {args.anchor_index} with {args.content}"
    if args.run:
        prompt += " and run it"

    print(f"Batch on {args.url} (anchor={args.anchor_index}, new={new_index})")
    result = execute_agentic_batch(
        tool_calls,
        user_prompt=prompt,
        url=args.url,
        tab_id=args.tab_id,
        registry=registry(),
        browser_tool_allowed=browser_tool_allowed,
        mode="agentic",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
