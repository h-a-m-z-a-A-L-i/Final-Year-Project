    #!/usr/bin/env python3
    """Standalone test runner for notebook_get_cells."""
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
        p.add_argument("--cell-indices", required=True, help="e.g. 1,2,3")
p.add_argument("--include-output", action="store_true")
        args = p.parse_args()
        payload: dict = {"url": args.url}
        payload["cell_indices"] = [int(x) for x in args.cell_indices.split(",") if x.strip()]
if args.include_output:
    payload["include_output"] = True
        result = run_tool(payload)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1


    if __name__ == "__main__":
        raise SystemExit(main())
