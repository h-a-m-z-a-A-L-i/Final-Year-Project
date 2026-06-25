"""One-time scaffold: one folder per tool with copied tool.py + test.py."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = ROOT.parent

BROWSER = {
    "click_cell": ("click_cell_tool.py", "run_click_cell"),
    "select_cell_by_index": ("select_cell_tool.py", "run_select_cell"),
    "insert_cell": ("insert_cell_tool.py", "run_insert_cell"),
    "edit_cell_by_index": ("edit_cell_tool.py", "run_edit_cell"),
    "run_cell": ("run_cell_tool.py", "run_run_cell"),
    "delete_by_index": ("delete_cell_tool.py", "run_delete_cell"),
    "creating_markdown_by_index": ("creating_markdown_tool.py", "run_creating_markdown"),
}

LOCAL = [
    "notebook_snapshot_status",
    "notebook_list_cells",
    "notebook_graph_query",
    "notebook_get_cell",
    "notebook_get_cells",
    "notebook_find_symbol",
    "notebook_search",
    "notebook_cell_neighbors",
    "notebook_recommend_placement",
    "notebook_overview",
    "notebook_executed_cells",
]

BOOTSTRAP = (
    "import sys\n"
    "from pathlib import Path\n"
    "_HOST = Path(__file__).resolve().parents[2]\n"
    "if str(_HOST) not in sys.path:\n"
    "    sys.path.insert(0, str(_HOST))\n"
    "\n"
)


def _inject_bootstrap(src: str) -> str:
    marker = "from __future__ import annotations\n"
    pos = src.find(marker)
    if pos >= 0:
        insert_at = pos + len(marker)
        return src[:insert_at] + BOOTSTRAP + src[insert_at:]
    return BOOTSTRAP + src

TEST_HEADER = textwrap.dedent(
    '''\
    #!/usr/bin/env python3
    """Standalone test runner for {name}."""
    from __future__ import annotations

    import argparse
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tool import TOOL, {runner}  # noqa: E402

    '''
)


def write_browser(name: str, src_file: str, runner: str) -> None:
    folder = ROOT / name
    folder.mkdir(parents=True, exist_ok=True)
    src = (HOST / src_file).read_text(encoding="utf-8")
    (folder / "tool.py").write_text(_inject_bootstrap(src), encoding="utf-8")
    extra = BROWSER_EXTRA_ARGS.get(name, "")
    test = TEST_HEADER.format(name=name, runner=runner) + textwrap.dedent(
        f'''\
        def main() -> int:
            p = argparse.ArgumentParser(description=f"Test {{TOOL}}")
            p.add_argument("--url", required=True)
            p.add_argument("--tab-id", type=int, default=None)
        {extra}
            args = p.parse_args()
            payload: dict = {{"url": args.url}}
            if args.tab_id is not None:
                payload["tab_id"] = args.tab_id
        {PAYLOAD_EXTRA.get(name, "")}
            result = {runner}(payload)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result.get("ok") else 1


        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )
    (folder / "test.py").write_text(test, encoding="utf-8")


def write_local(name: str) -> None:
    folder = ROOT / name
    folder.mkdir(parents=True, exist_ok=True)
    tool_py = textwrap.dedent(
        f'''\
        """Isolated local tool: {name} (from local_notebook_tools)."""
        import sys
        from pathlib import Path

        _REPO = Path(__file__).resolve().parents[4]
        if str(_REPO) not in sys.path:
            sys.path.insert(0, str(_REPO))

        from testing.host.local_notebook_tools import {name} as run_tool

        TOOL = "{name}"
        '''
    )
    (folder / "tool.py").write_text(tool_py, encoding="utf-8")
    extra = LOCAL_EXTRA_ARGS.get(name, "")
    test = textwrap.dedent(
        f'''\
        #!/usr/bin/env python3
        """Standalone test runner for {name}."""
        from __future__ import annotations

        import argparse
        import json
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from tool import TOOL, run_tool  # noqa: E402


        def main() -> int:
            p = argparse.ArgumentParser(description=f"Test {{TOOL}}")
            p.add_argument("--url", required=True)
        {extra}
            args = p.parse_args()
            payload: dict = {{"url": args.url}}
        {LOCAL_PAYLOAD_EXTRA.get(name, "")}
            result = run_tool(payload)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result.get("ok") else 1


        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )
    (folder / "test.py").write_text(test, encoding="utf-8")


BROWSER_EXTRA_ARGS = {
    "click_cell": '    p.add_argument("--cell-index", type=int, required=True)\n    p.add_argument("--dom-index", type=int, default=None)',
    "select_cell_by_index": "    p.add_argument(\"--cell-index\", type=int, required=True)",
    "insert_cell": '    p.add_argument("--index", type=int, required=True)\n    p.add_argument("--direction", default="below", choices=["below", "above"])',
    "edit_cell_by_index": '    p.add_argument("--cell-index", type=int, required=True)\n    p.add_argument("--content", required=True)',
    "run_cell": "    p.add_argument(\"--cell-index\", type=int, required=True)",
    "delete_by_index": "    p.add_argument(\"--cell-index\", type=int, required=True)",
    "creating_markdown_by_index": "    p.add_argument(\"--index\", type=int, required=True)",
}

PAYLOAD_EXTRA = {
    "click_cell": "    payload[\"cell_index\"] = args.cell_index\n    if args.dom_index is not None:\n        payload[\"dom_index\"] = args.dom_index",
    "select_cell_by_index": '    payload["cell_index"] = args.cell_index',
    "insert_cell": '    payload["index"] = args.index\n    payload["direction"] = args.direction',
    "edit_cell_by_index": '    payload["cell_index"] = args.cell_index\n    payload["content"] = args.content',
    "run_cell": '    payload["cell_index"] = args.cell_index',
    "delete_by_index": '    payload["cell_index"] = args.cell_index',
    "creating_markdown_by_index": '    payload["index"] = args.index',
}

LOCAL_EXTRA_ARGS = {
    "notebook_get_cell": '    p.add_argument("--cell-index", type=int, required=True)\n    p.add_argument("--include-output", action="store_true")',
    "notebook_get_cells": '    p.add_argument("--cell-indices", required=True, help="e.g. 1,2,3")\n    p.add_argument("--include-output", action="store_true")',
    "notebook_find_symbol": '    p.add_argument("--symbol", required=True)',
    "notebook_search": '    p.add_argument("--query", required=True)\n    p.add_argument("--regex", action="store_true")',
    "notebook_cell_neighbors": "    p.add_argument(\"--cell-index\", type=int, required=True)",
    "notebook_recommend_placement": '    p.add_argument("--symbol", default=None)\n    p.add_argument("--symbols", default=None)',
    "notebook_list_cells": "    p.add_argument(\"--preview-chars\", type=int, default=None)",
}

LOCAL_PAYLOAD_EXTRA = {
    "notebook_get_cell": '    payload["cell_index"] = args.cell_index\n    if args.include_output:\n        payload["include_output"] = True',
    "notebook_get_cells": '    payload["cell_indices"] = [int(x) for x in args.cell_indices.split(",") if x.strip()]\n    if args.include_output:\n        payload["include_output"] = True',
    "notebook_find_symbol": '    payload["symbol"] = args.symbol',
    "notebook_search": '    payload["query"] = args.query\n    if args.regex:\n        payload["regex"] = True',
    "notebook_cell_neighbors": '    payload["cell_index"] = args.cell_index',
    "notebook_recommend_placement": '    if args.symbol:\n        payload["symbol"] = args.symbol\n    if args.symbols:\n        payload["symbols"] = [s.strip() for s in args.symbols.split(",") if s.strip()]',
    "notebook_list_cells": "    if args.preview_chars is not None:\n        payload[\"preview_chars\"] = args.preview_chars",
}


def main() -> None:
    for name, (src, runner) in BROWSER.items():
        write_browser(name, src, runner)
    for name in LOCAL:
        write_local(name)
    print(f"Scaffolded {len(BROWSER) + len(LOCAL)} tool folders under {ROOT}")


if __name__ == "__main__":
    main()
