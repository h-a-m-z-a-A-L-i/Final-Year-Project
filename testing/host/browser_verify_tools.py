"""Standalone browser verification entry points (snapshot + hash; no dispatch ack)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

try:
    from .bot_tool_utils import pick_notebook_url
    from .browser_tool_response import tool_failure, tool_success
    from .creating_markdown_verification import (
        capture_markdown_baseline,
        wait_for_markdown_verification,
    )
    from .delete_cell_verification import capture_delete_baseline, wait_for_delete_verification
    from .edit_cell_verification import capture_edit_baseline, wait_for_edit_verification
    from .insert_cell_verification import capture_insert_baseline, wait_for_insert_verification
    from .run_cell_verification import capture_run_baseline, wait_for_run_verification
    from .select_cell_verification import wait_for_select_verification
except Exception:
    from bot_tool_utils import pick_notebook_url  # type: ignore
    from browser_tool_response import tool_failure, tool_success  # type: ignore
    from creating_markdown_verification import (  # type: ignore
        capture_markdown_baseline,
        wait_for_markdown_verification,
    )
    from delete_cell_verification import capture_delete_baseline, wait_for_delete_verification  # type: ignore
    from edit_cell_verification import capture_edit_baseline, wait_for_edit_verification  # type: ignore
    from insert_cell_verification import capture_insert_baseline, wait_for_insert_verification  # type: ignore
    from run_cell_verification import capture_run_baseline, wait_for_run_verification  # type: ignore
    from select_cell_verification import wait_for_select_verification  # type: ignore


def _resolve_before_snapshot(args: dict, *, capture: Callable[[], dict] | None = None) -> dict | None:
    raw = args.get("before_snapshot")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    before_file = args.get("before_file")
    if before_file:
        path = Path(str(before_file))
        if path.is_file():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    if "snapshot" in parsed and isinstance(parsed["snapshot"], dict):
                        return parsed["snapshot"]
                    return parsed
            except (OSError, json.JSONDecodeError):
                return None

    if args.get("capture_baseline") and capture is not None:
        baseline = capture()
        snap = baseline.get("snapshot")
        return snap if isinstance(snap, dict) else baseline
    return None


def _timeout(args: dict, default: float) -> float:
    try:
        return float(args.get("timeout") or default)
    except (TypeError, ValueError):
        return default


def _failure_from_wait(tool: str, result: dict, default: str) -> dict:
    err = str(result.get("error") or default)
    fields = {k: v for k, v in result.items() if k not in ("ok", "error")}
    return tool_failure(tool, err, **fields)


def run_verify_edit_cell(args: dict) -> dict:
    tool = "verify_edit_cell"
    url = pick_notebook_url(args)
    if not url:
        return tool_failure(tool, "url is required")

    cell_index = args.get("cell_index")
    content = str(args.get("content") or "")
    if cell_index is None:
        return tool_failure(tool, "cell_index is required")
    if not content.strip():
        return tool_failure(tool, "content is required")

    before_input = str(args.get("before_input") or "")
    before_hash = str(args.get("before_hash") or "")
    if args.get("capture_baseline"):
        baseline = capture_edit_baseline(url, int(cell_index))
        before_input = str(baseline.get("before_input") or before_input)
        before_hash = str(baseline.get("before_hash") or before_hash)

    dom_index = args.get("dom_index")
    if dom_index is None:
        dom_index = int(cell_index) - 1

    result = wait_for_edit_verification(
        url,
        int(cell_index),
        content,
        before_input=before_input,
        before_hash=before_hash,
        dom_index=int(dom_index),
        tab_id=args.get("tab_id"),
        timeout=_timeout(args, 15.0),
    )
    if result.get("ok"):
        return tool_success(tool, **result)
    return _failure_from_wait(tool, result, "edit not verified")


def run_verify_select_cell(args: dict) -> dict:
    tool = "verify_select_cell"
    url = pick_notebook_url(args)
    if not url:
        return tool_failure(tool, "url is required")

    app_index = args.get("cell_index") or args.get("app_index")
    if app_index is None:
        return tool_failure(tool, "cell_index is required")

    dom_index = args.get("dom_index")
    if dom_index is None:
        dom_index = int(app_index) - 1

    result = wait_for_select_verification(
        url,
        dom_index=int(dom_index),
        app_index=int(app_index),
        tab_id=args.get("tab_id"),
        timeout=_timeout(args, 8.0),
    )
    if result.get("ok"):
        return tool_success(tool, **result)
    return _failure_from_wait(tool, result, "select not verified")


def run_verify_run_cell(args: dict) -> dict:
    tool = "verify_run_cell"
    url = pick_notebook_url(args)
    if not url:
        return tool_failure(tool, "url is required")

    cell_index = args.get("cell_index")
    if cell_index is None:
        return tool_failure(tool, "cell_index is required")
    idx = int(cell_index)

    before_data = _resolve_before_snapshot(
        args,
        capture=lambda: capture_run_baseline(url, idx),
    )
    before_cell = None
    host_log_offset = int(args.get("host_log_offset") or 0)
    if isinstance(before_data, dict):
        cells = before_data.get("cells") or []
        for cell in cells:
            if isinstance(cell, dict) and int(cell.get("index", -1)) == idx:
                before_cell = cell
                break
    elif args.get("capture_baseline"):
        baseline = capture_run_baseline(url, idx)
        before_data = baseline.get("snapshot") if isinstance(baseline.get("snapshot"), dict) else {}
        before_cell = baseline.get("before_cell")
        host_log_offset = int(baseline.get("host_log_offset") or 0)

    result = wait_for_run_verification(
        url,
        idx,
        before_data if isinstance(before_data, dict) else {},
        before_cell=before_cell,
        host_log_offset=host_log_offset,
        timeout=_timeout(args, 30.0),
        tab_id=args.get("tab_id"),
    )
    if result.get("ok"):
        return tool_success(tool, **result)
    return _failure_from_wait(tool, result, "run not verified")


def run_verify_insert_cell(args: dict) -> dict:
    tool = "verify_insert_cell"
    url = pick_notebook_url(args)
    if not url:
        return tool_failure(tool, "url is required")

    anchor = args.get("index") or args.get("cell_index")
    if anchor is None:
        return tool_failure(tool, "index (anchor cell) is required")

    direction = str(args.get("direction") or "below")
    expected_new_index = args.get("expected_new_index")
    if expected_new_index is None and direction == "below":
        expected_new_index = int(anchor) + 1

    before_data = _resolve_before_snapshot(
        args,
        capture=lambda: capture_insert_baseline(url, int(anchor), direction=direction),
    )
    if before_data is None:
        return tool_failure(
            tool,
            "before snapshot required — pass before_file=, before_snapshot=, or capture_baseline=1 before dispatch",
        )

    result = wait_for_insert_verification(
        url,
        before_data,
        expected_new_index=int(expected_new_index) if expected_new_index is not None else None,
        anchor_index=int(anchor),
        direction=direction,
        expected_content=str(args.get("content") or args.get("expected_content") or ""),
        timeout=_timeout(args, 15.0),
    )
    if result.get("ok"):
        return tool_success(tool, **result)
    return _failure_from_wait(tool, result, "insert not verified")


def run_verify_delete_cell(args: dict) -> dict:
    tool = "verify_delete_cell"
    url = pick_notebook_url(args)
    if not url:
        return tool_failure(tool, "url is required")

    cell_index = args.get("cell_index")
    if cell_index is None:
        return tool_failure(tool, "cell_index is required")

    before_data = _resolve_before_snapshot(
        args,
        capture=lambda: capture_delete_baseline(url, int(cell_index)),
    )
    if before_data is None:
        return tool_failure(
            tool,
            "before snapshot required — pass before_file=, before_snapshot=, or capture_baseline=1 before dispatch",
        )

    result = wait_for_delete_verification(
        url,
        before_data,
        int(cell_index),
        timeout=_timeout(args, 15.0),
    )
    if result.get("ok"):
        return tool_success(tool, **result)
    return _failure_from_wait(tool, result, "delete not verified")


def run_verify_creating_markdown(args: dict) -> dict:
    tool = "verify_creating_markdown"
    url = pick_notebook_url(args)
    if not url:
        return tool_failure(tool, "url is required")

    anchor = args.get("index") or args.get("cell_index")
    if anchor is None:
        return tool_failure(tool, "index (anchor cell) is required")

    expected_index = args.get("expected_index")
    if expected_index is None:
        expected_index = int(anchor) + 1

    before_data = _resolve_before_snapshot(
        args,
        capture=lambda: capture_markdown_baseline(url, int(anchor)),
    )
    if before_data is None:
        return tool_failure(
            tool,
            "before snapshot required — pass before_file=, before_snapshot=, or capture_baseline=1 before dispatch",
        )

    result = wait_for_markdown_verification(
        url,
        before_data,
        expected_index=int(expected_index),
        anchor_index=int(anchor),
        expected_content=str(args.get("content") or args.get("expected_content") or ""),
        timeout=_timeout(args, 15.0),
    )
    if result.get("ok"):
        return tool_success(tool, **result)
    return _failure_from_wait(tool, result, "markdown not verified")
