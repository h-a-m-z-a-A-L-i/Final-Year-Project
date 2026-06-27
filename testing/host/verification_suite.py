"""Orchestrate capture → dispatch → verify for browser notebook tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from .creating_markdown_verification import capture_markdown_baseline
    from .delete_cell_verification import capture_delete_baseline
    from .edit_cell_verification import capture_edit_baseline
    from .insert_cell_verification import capture_insert_baseline
    from .run_cell_verification import capture_run_baseline
except Exception:
    from creating_markdown_verification import capture_markdown_baseline  # type: ignore
    from delete_cell_verification import capture_delete_baseline  # type: ignore
    from edit_cell_verification import capture_edit_baseline  # type: ignore
    from insert_cell_verification import capture_insert_baseline  # type: ignore
    from run_cell_verification import capture_run_baseline  # type: ignore

_DISPATCH_TOOLS = frozenset({
    "select_cell_by_index",
    "insert_cell",
    "edit_cell_by_index",
    "run_cell",
    "delete_by_index",
    "creating_markdown_by_index",
})


def _dispatch_for(tool: str):
    if tool == "select_cell_by_index":
        from .select_cell_tool import run_select_cell
        return run_select_cell
    if tool == "insert_cell":
        from .insert_cell_tool import run_insert_cell
        return run_insert_cell
    if tool == "edit_cell_by_index":
        from .edit_cell_tool import run_edit_cell
        return run_edit_cell
    if tool == "run_cell":
        from .run_cell_tool import run_run_cell
        return run_run_cell
    if tool == "delete_by_index":
        from .delete_cell_tool import run_delete_cell
        return run_delete_cell
    if tool == "creating_markdown_by_index":
        from .creating_markdown_tool import run_creating_markdown
        return run_creating_markdown
    return None


def _verify_for(tool: str):
    from . import browser_verify_tools as bvt

    mapping = {
        "select_cell_by_index": bvt.run_verify_select_cell,
        "insert_cell": bvt.run_verify_insert_cell,
        "edit_cell_by_index": bvt.run_verify_edit_cell,
        "run_cell": bvt.run_verify_run_cell,
        "delete_by_index": bvt.run_verify_delete_cell,
        "creating_markdown_by_index": bvt.run_verify_creating_markdown,
    }
    return mapping.get(tool)


def _baseline_for(tool: str, args: dict) -> dict[str, Any]:
    url = str(args.get("url") or "")
    if tool == "select_cell_by_index":
        return {}
    if tool == "edit_cell_by_index":
        return capture_edit_baseline(url, int(args["cell_index"]))
    if tool == "run_cell":
        return capture_run_baseline(url, int(args["cell_index"]))
    if tool == "insert_cell":
        return capture_insert_baseline(
            url,
            int(args.get("index") or args.get("cell_index")),
            direction=str(args.get("direction") or "below"),
        )
    if tool == "delete_by_index":
        return capture_delete_baseline(url, int(args["cell_index"]))
    if tool == "creating_markdown_by_index":
        return capture_markdown_baseline(url, int(args.get("index") or args.get("cell_index")))
    return {}


def _verify_args(tool: str, args: dict, baseline: dict[str, Any]) -> dict:
    verify_args = dict(args)
    snap = baseline.get("snapshot")
    if isinstance(snap, dict):
        verify_args["before_snapshot"] = snap
    if tool == "run_cell":
        verify_args["host_log_offset"] = int(baseline.get("host_log_offset") or 0)
    if tool == "edit_cell_by_index":
        verify_args["before_input"] = baseline.get("before_input", "")
        verify_args["before_hash"] = baseline.get("before_hash", "")
    return verify_args


def run_dispatch_and_verify(tool: str, args: dict) -> dict[str, Any]:
    """Capture baseline, dispatch tool, then poll verification."""
    if tool not in _DISPATCH_TOOLS:
        return {"ok": False, "error": f"unsupported tool: {tool}", "tool": tool}

    dispatch_fn = _dispatch_for(tool)
    verify_fn = _verify_for(tool)
    if dispatch_fn is None or verify_fn is None:
        return {"ok": False, "error": f"unsupported tool: {tool}", "tool": tool}

    baseline = _baseline_for(tool, args)
    dispatch = dispatch_fn(args)
    if not dispatch.get("ok"):
        return {
            "ok": False,
            "tool": tool,
            "phase": "dispatch",
            "dispatch": dispatch,
            "error": dispatch.get("error") or "dispatch failed",
        }

    verify = verify_fn(_verify_args(tool, args, baseline))
    return {
        "ok": bool(verify.get("ok")),
        "tool": tool,
        "phase": "verified" if verify.get("ok") else "verify_failed",
        "dispatch": dispatch,
        "verify": verify,
        "baseline_source": baseline.get("snapshot_source"),
    }


def save_baseline(baseline: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
