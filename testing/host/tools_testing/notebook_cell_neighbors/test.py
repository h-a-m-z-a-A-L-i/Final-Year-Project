#!/usr/bin/env python3
"""Standalone test runner for notebook_cell_neighbors."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool import TOOL, run_tool  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=f"Test {TOOL}")
    p.add_argument("--url", required=True)
    p.add_argument("--cell-index", type=int, required=True)
    args = p.parse_args()
    payload: dict = {"url": args.url}
    payload["cell_index"] = args.cell_index
    result = run_tool(payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
