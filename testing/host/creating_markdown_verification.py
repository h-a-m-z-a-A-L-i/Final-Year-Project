"""Verify creating_markdown_by_index via live notebook snapshot + hash."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

MARKDOWN_VERIFY_TIMEOUT_SEC = float(os.environ.get("MARKDOWN_CELL_VERIFY_TIMEOUT_SEC", "15"))
MARKDOWN_VERIFY_POLL_SEC = float(os.environ.get("MARKDOWN_CELL_VERIFY_POLL_SEC", "0.05"))


def _normalize_cell_source(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").strip()


def _source_hash(text: str) -> str:
    norm = _normalize_cell_source(text)
    return hashlib.sha256(norm.encode("utf-8", errors="replace")).hexdigest()[:16]


def capture_markdown_baseline(url: str, anchor_index: int) -> dict[str, Any]:
    try:
        from .persistent_notebook_verify import load_persistent_notebook_snapshot
    except Exception:
        from persistent_notebook_verify import load_persistent_notebook_snapshot  # type: ignore

    data, source = load_persistent_notebook_snapshot(url)
    anchor = int(anchor_index)
    return {
        "url": url,
        "anchor_index": anchor,
        "expected_new_index": anchor + 1,
        "snapshot": data if isinstance(data, dict) else {},
        "snapshot_source": source,
        "before_cells": list((data or {}).get("cells") or []) if isinstance(data, dict) else [],
        "cell_count": len((data or {}).get("cells") or []) if isinstance(data, dict) else 0,
    }


def wait_for_markdown_verification(
    url: str,
    before_data: dict | None,
    *,
    expected_index: int | None = None,
    anchor_index: int | None = None,
    expected_content: str = "",
    timeout: float = MARKDOWN_VERIFY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Confirm a new markdown cell appeared at the expected index in persistent JSON."""
    try:
        from .cell_structure_observer import verify_cell_at_index, verify_cell_count_increased
        from .persistent_notebook_verify import poll_persistent_snapshot, report_verify_event, source_hash
    except Exception:
        from cell_structure_observer import verify_cell_at_index, verify_cell_count_increased  # type: ignore
        from persistent_notebook_verify import poll_persistent_snapshot, report_verify_event, source_hash  # type: ignore

    before_cells = (before_data or {}).get("cells") if isinstance(before_data, dict) else None
    if before_cells is None:
        before_cells = []

    anchor = int(anchor_index) if anchor_index is not None else None
    want_index = int(expected_index) if expected_index is not None else (anchor + 1 if anchor is not None else None)
    want_hash = source_hash(expected_content) if expected_content else ""
    last_check: dict[str, Any] = {}

    report_verify_event("markdown_watch", url=url, anchor_index=anchor, expected_index=want_index)

    def _on_tick(data: dict | None, _struct_hash: str, _mtime: float) -> dict[str, Any] | None:
        nonlocal last_check
        after_cells = list((data or {}).get("cells") or []) if isinstance(data, dict) else []
        check = verify_cell_count_increased(
            before_cells,
            after_cells,
            expected_delta=1,
            expected_indices=[want_index] if want_index is not None else None,
        )
        last_check = check
        if not check.get("ok"):
            return None
        if want_index is not None:
            type_check = verify_cell_at_index(after_cells, want_index, cell_type="markdown")
            if not type_check.get("ok"):
                return None
            if want_hash:
                current = ""
                for cell in after_cells:
                    if isinstance(cell, dict) and int(cell.get("index", -1)) == want_index:
                        current = str(cell.get("input") or cell.get("source") or "")
                        break
                if source_hash(current) != want_hash:
                    return None
        return {
            "ok": True,
            "markdown_verified": True,
            "wait_reason": "persistent_snapshot_markdown",
            "anchor_index": anchor,
            "expected_index": want_index,
            "new_indices": check.get("new_indices"),
            "count_before": check.get("count_before"),
            "count_after": check.get("count_after"),
            "content_hash": want_hash or None,
        }

    result = poll_persistent_snapshot(url, timeout=timeout, on_tick=_on_tick)
    if result.get("ok"):
        return result

    err = (
        f"markdown insert not verified in persistent JSON within {timeout}s"
        + (f" (expected markdown at index {want_index})" if want_index is not None else "")
    )
    if last_check:
        err += (
            f" — count {last_check.get('count_before')}->{last_check.get('count_after')}"
            f", new_indices={last_check.get('new_indices')}"
        )
    return {
        "ok": False,
        "markdown_verified": False,
        "error": err,
        "anchor_index": anchor,
        "expected_index": want_index,
        "last_check": last_check or None,
    }
