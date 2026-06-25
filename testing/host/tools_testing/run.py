#!/usr/bin/env python3
"""One command for every tool — compact args; no LLM (tool-test mode)."""
from __future__ import annotations

import os

# Tool tests must not initialize LLM / Gemini (no agent, no streaming).
os.environ["NOTEBOOK_COPILOT_TOOL_TEST"] = "1"

import json
import sys
from pathlib import Path

# Usage:
#   python testing/host/tools_testing/run.py list
#   python testing/host/tools_testing/run.py check
#   python testing/host/tools_testing/run.py tabs
#   python testing/host/tools_testing/run.py run_cell url=https://... cell=3
#   python testing/host/tools_testing/run.py run_cell cell=3   # auto-fills url+tab from last session

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SPEC_PATH = ROOT / "tools_compact.json"

_KEY_ALIASES = {
    "tab": "tab_id",
    "tabId": "tab_id",
    "cell": "cell_index",
    "cellIndex": "cell_index",
    "cells": "cell_indices",
    "tab_url": "url",
    "tabUrl": "url",
}

_INDEX_TOOLS = frozenset({"insert_cell", "creating_markdown_by_index"})

_BROWSER_TOOLS = frozenset({
    "select_cell_by_index",
    "insert_cell",
    "edit_cell_by_index",
    "run_cell",
    "delete_by_index",
    "creating_markdown_by_index",
})

# Chrome tab ids are large integers; tab=1 is invalid and blocks URL auto-match.
_MIN_VALID_TAB_ID = 50_000

def _load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _normalize(tool: str, raw: dict) -> dict:
    out: dict = {}
    for k, v in raw.items():
        if k == "tool":
            continue
        canon = _KEY_ALIASES.get(k, k)
        out[canon] = v
    if tool in _INDEX_TOOLS:
        if "cell_index" in out and "index" not in out:
            out["index"] = out.pop("cell_index")
    if tool in _BROWSER_TOOLS:
        tid = out.get("tab_id")
        if isinstance(tid, int) and tid < _MIN_VALID_TAB_ID:
            out.pop("tab_id", None)
    return out


def _parse_kv_tokens(tokens: list[str]) -> dict:
    out: dict = {}
    for tok in tokens:
        if "=" not in tok:
            continue
        k, _, v = tok.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if k in ("cells", "symbols"):
            out[k] = [int(x) for x in v.split(",") if x.strip().isdigit()] if k == "cells" else [s.strip() for s in v.split(",") if s.strip()]
        elif k in ("tab", "tab_id", "cell", "cell_index", "index"):
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


def _parse_args(tool: str, rest: list[str]) -> tuple[dict | None, str | None]:
    if not rest:
        return None, "missing args"
    blob = " ".join(rest).strip()
    if blob.startswith("{"):
        try:
            return json.loads(blob), None
        except json.JSONDecodeError as e:
            return None, f"invalid json: {e}"
    if "=" in blob:
        return _parse_kv_tokens(rest), None
    return None, "use json {...} or key=value pairs (url=... tab=1 cell=3)"


def _missing(tool: str, args: dict, spec: dict) -> list[str]:
    row = (spec.get("tools") or {}).get(tool) or {}
    need = list(row.get("need") or [])
    miss = []
    for key in need:
        if key == "url|tab":
            has_url = bool(str(args.get("url") or "").strip())
            tid = args.get("tab_id")
            has_tab = isinstance(tid, int) and tid >= _MIN_VALID_TAB_ID
            if not has_url and not has_tab:
                miss.append("url|tab")
            continue
        if key == "url" and not str(args.get("url") or "").strip():
            miss.append("url")
        elif key == "cell" and args.get("cell_index") is None:
            miss.append("cell")
        elif key == "index" and args.get("index") is None:
            miss.append("index")
        elif key == "content" and not str(args.get("content") or "").strip():
            miss.append("content")
        elif key == "symbol" and not str(args.get("symbol") or "").strip():
            miss.append("symbol")
        elif key == "query" and not str(args.get("query") or "").strip():
            miss.append("query")
        elif key == "cells" and not args.get("cell_indices"):
            miss.append("cells")
    return miss


def _preflight_browser(tool: str, args: dict) -> dict | None:
    if tool not in _BROWSER_TOOLS:
        return None
    try:
        from testing.host.config import BOT_COMMANDS_PATH
        from testing.host.browser_target_context import (
            discover_browser_tabs,
            is_host_extension_live,
            stale_tab_hint,
        )
    except Exception:
        from config import BOT_COMMANDS_PATH  # type: ignore
        from browser_target_context import (  # type: ignore
            discover_browser_tabs,
            is_host_extension_live,
            stale_tab_hint,
        )
    if not BOT_COMMANDS_PATH.parent.exists():
        return {
            "ok": False,
            "tool": tool,
            "error": (
                "Browser tool test requires host.py running and extension connected. "
                "Start: python testing/host/host.py — then open the notebook /edit page."
            ),
        }
    if not is_host_extension_live():
        return {
            "ok": False,
            "tool": tool,
            "error": (
                "Extension not connected to host (no recent NOTEBOOK_DATA). "
                "Start host.py, open a Kaggle /edit tab, reload the Chrome extension, then run tabs."
            ),
            "hint": "python testing/host/tools_testing/run.py tabs",
        }
    stale = stale_tab_hint(args.get("tab_id"))
    if stale:
        tabs = discover_browser_tabs()[:3]
        return {
            "ok": False,
            "tool": tool,
            "error": stale,
            "known_tabs": tabs,
            "hint": "python testing/host/tools_testing/run.py tabs",
        }
    if not str(args.get("url") or "").strip():
        tid = args.get("tab_id")
        if not (isinstance(tid, int) and tid >= _MIN_VALID_TAB_ID):
            return {
                "ok": False,
                "tool": tool,
                "error": "browser tools need url= or tab= (one is enough)",
                "hint": "python testing/host/tools_testing/run.py tabs",
            }
    return None


def _dispatch(tool: str, args: dict) -> dict:
    folder = ROOT / tool
    tool_py = folder / "tool.py"
    if tool_py.is_file():
        import importlib.util

        spec = importlib.util.spec_from_file_location(f"tt_{tool}", tool_py)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load {tool_py}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for attr in (
            "run_select_cell",
            "run_insert_cell",
            "run_edit_cell",
            "run_run_cell",
            "run_delete_cell",
            "run_creating_markdown",
            "run_tool",
        ):
            fn = getattr(mod, attr, None)
            if callable(fn):
                return fn(args)
    from testing.host.tool_registry import registry

    return registry().call(tool, args)


def _cmd_tabs() -> int:
    try:
        from testing.host.browser_target_context import discover_browser_tabs, last_browser_target
    except Exception:
        from browser_target_context import discover_browser_tabs, last_browser_target  # type: ignore

    tabs = discover_browser_tabs()
    last = last_browser_target()
    print(
        json.dumps(
            {
                "ok": True,
                "count": len(tabs),
                "last": last,
                "tabs": tabs,
                "usage": (
                    "python testing/host/tools_testing/run.py run_cell "
                    "url=<url> cell=1  OR  tab=<tabId> cell=1"
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _auto_fill_browser(tool: str, args: dict) -> tuple[dict, dict | None]:
    if tool not in _BROWSER_TOOLS:
        return args, None
    try:
        from testing.host.browser_target_context import auto_fill_browser_args, stale_tab_hint
    except Exception:
        from browser_target_context import auto_fill_browser_args, stale_tab_hint  # type: ignore

    filled, hint = auto_fill_browser_args(args)
    return filled, hint


def _enrich_browser_error(tool: str, args: dict, result: dict) -> dict:
    if tool not in _BROWSER_TOOLS or result.get("ok"):
        return result
    err = str(result.get("error") or "")
    lowered = err.lower()
    if "browser tab is not available" in lowered or "no open browser tab" in lowered:
        try:
            from testing.host.browser_target_context import discover_browser_tabs
        except Exception:
            from browser_target_context import discover_browser_tabs  # type: ignore
        tabs = discover_browser_tabs()[:5]
        if tabs:
            result = dict(result)
            result["hint"] = (
                "Stale or wrong tab/url. Known notebook tabs: "
                + "; ".join(f"tab={t['tabId']} url={t['url']}" for t in tabs)
                + ". Run: python testing/host/tools_testing/run.py tabs"
            )
    return result


def _cmd_check() -> int:
    try:
        from testing.host.config import BOT_COMMANDS_PATH, BOT_RESULTS_PATH, DATA_ROOT
    except Exception:
        print(json.dumps({"ok": False, "error": "cannot load host paths"}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "tool_test",
                "llm": "disabled",
                "host_queue": str(BOT_COMMANDS_PATH),
                "host_queue_exists": BOT_COMMANDS_PATH.is_file(),
                "data_root": str(DATA_ROOT),
                "browser_tools": sorted(_BROWSER_TOOLS),
                "hint": "Local tools: url= only. Browser: host.py + extension. Run tabs for live tab ids.",
            },
            indent=2,
        )
    )
    return 0


def _cmd_list() -> int:
    spec = _load_spec()
    print(spec.get("format", ""))
    print("\nLocal (JSON snapshot only — no host, no LLM):")
    for name, row in sorted((spec.get("tools") or {}).items()):
        if name in _BROWSER_TOOLS:
            continue
        need = ",".join(row.get("need") or [])
        opt = row.get("opt") or []
        opt_s = f" [{','.join(opt)}]" if opt else ""
        print(f"  {name}: {need}{opt_s}")
    print("\nBrowser (host.py + extension; url= required; tab= optional real Chrome id):")
    for name, row in sorted((spec.get("tools") or {}).items()):
        if name not in _BROWSER_TOOLS:
            continue
        need = ",".join(row.get("need") or [])
        opt = row.get("opt") or []
        opt_s = f" [{','.join(opt)}]" if opt else ""
        print(f"  {name}: {need}{opt_s}")
    return 0


def _cmd_schema(tool: str) -> int:
    spec = _load_spec()
    tools = spec.get("tools") or {}
    if tool not in tools:
        print(json.dumps({"error": f"unknown tool: {tool}", "known": sorted(tools)}))
        return 1
    row = tools[tool]
    print(
        json.dumps(
            {
                "tool": tool,
                "command": f"python testing/host/tools_testing/run.py {tool} url=... tab=1 cell=3",
                "required": row.get("need"),
                "optional": row.get("opt"),
                "keys": spec.get("keys"),
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if args[0] == "list":
        return _cmd_list()
    if args[0] == "check":
        return _cmd_check()
    if args[0] == "tabs":
        return _cmd_tabs()
    if args[0] == "schema":
        if len(args) < 2:
            print("usage: run.py schema <tool>", file=sys.stderr)
            return 2
        return _cmd_schema(args[1])

    if args[0].startswith("{"):
        try:
            payload = json.loads(args[0])
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"invalid json: {e}"}))
            return 1
        tool = str(payload.get("tool") or "").strip()
        raw = payload
    else:
        tool = args[0].strip()
        if len(args) < 2:
            print(json.dumps({"ok": False, "error": "missing args"}))
            return 1
        raw, err = _parse_args(tool, args[1:])
        if err:
            print(json.dumps({"ok": False, "error": err}))
            return 1

    spec = _load_spec()
    tools = spec.get("tools") or {}
    if tool not in tools:
        print(json.dumps({"ok": False, "error": f"unknown tool: {tool}", "known": sorted(tools)}))
        return 1

    norm = _normalize(tool, raw if isinstance(raw, dict) else {})
    norm, auto_hint = _auto_fill_browser(tool, norm)
    miss = _missing(tool, norm, spec)
    if miss:
        payload: dict = {"ok": False, "error": f"missing: {', '.join(miss)}", "tool": tool}
        if tool in _BROWSER_TOOLS:
            payload["hint"] = "Run: python testing/host/tools_testing/run.py tabs"
        print(json.dumps(payload))
        return 1

    if auto_hint:
        sys.stderr.write(json.dumps(auto_hint, ensure_ascii=False) + "\n")

    pre = _preflight_browser(tool, norm)
    if pre:
        print(json.dumps(pre, ensure_ascii=False))
        return 1

    try:
        result = _dispatch(tool, norm)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "tool": tool}))
        return 1

    result = _enrich_browser_error(tool, norm, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
