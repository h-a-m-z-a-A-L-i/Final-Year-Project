#!/usr/bin/env python3
"""Standalone test runner for creating_markdown_by_index."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool import TOOL, run_creating_markdown  # noqa: E402

def main() -> int:
    p = argparse.ArgumentParser(description=f"Test {TOOL}")
    p.add_argument("--url", required=True)
    p.add_argument("--tab-id", type=int, default=None)
    p.add_argument("--index", type=int, required=True)
    args = p.parse_args()
    payload: dict = {"url": args.url}
    if args.tab_id is not None:
        payload["tab_id"] = args.tab_id
    payload["index"] = args.index
    result = run_creating_markdown(payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
