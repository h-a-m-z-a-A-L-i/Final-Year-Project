    #!/usr/bin/env python3
    """Standalone test runner for notebook_search."""
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
        p.add_argument("--query", required=True)
p.add_argument("--regex", action="store_true")
        args = p.parse_args()
        payload: dict = {"url": args.url}
        payload["query"] = args.query
if args.regex:
    payload["regex"] = True
        result = run_tool(payload)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1


    if __name__ == "__main__":
        raise SystemExit(main())
