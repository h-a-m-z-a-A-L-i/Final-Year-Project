"""Verify insert_cell via live notebook snapshot + hash (not extension ack)."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

INSERT_VERIFY_TIMEOUT_SEC = float(os.environ.get("INSERT_CELL_VERIFY_TIMEOUT_SEC", "15"))
INSERT_VERIFY_POLL_SEC = float(os.environ.get("INSERT_CELL_VERIFY_POLL_SEC", "0.05"))


def _normalize_cell_source(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").strip()


def _source_hash(text: str) -> str:
    norm = _normalize_cell_source(text)
    return hashlib.sha256(norm.encode("utf-8", errors="replace")).hexdigest()[:16]


def capture_insert_baseline(url: str, anchor_index: int, *, direction: str = "below") -> dict[str, Any]:
    try:
        from .persistent_notebook_verify import load_persistent_notebook_snapshot
    except Exception:
        from persistent_notebook_verify import load_persistent_notebook_snapshot  # type: ignore

    data, source = load_persistent_notebook_snapshot(url)
    anchor = int(anchor_index)
    direction_norm = str(direction or "below").strip().lower()
    expected_new_index = anchor + 1 if direction_norm == "below" else anchor
    return {
        "url": url,
        "anchor_index": anchor,
        "direction": direction_norm,
        "expected_new_index": expected_new_index,
        "snapshot": data if isinstance(data, dict) else {},
        "snapshot_source": source,
        "before_cells": list((data or {}).get("cells") or []) if isinstance(data, dict) else [],
        "cell_count": len((data or {}).get("cells") or []) if isinstance(data, dict) else 0,
    }


def wait_for_insert_verification(
    url: str,
    before_data: dict | None,
    *,
    expected_new_index: int | None = None,
    anchor_index: int | None = None,
    direction: str = "below",
    expected_content: str = "",
    timeout: float = INSERT_VERIFY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Confirm notebook gained a cell at the expected index with optional content hash (persistent JSON)."""
    try:
        from .cell_structure_observer import verify_cell_count_increased
        from .persistent_notebook_verify import poll_persistent_snapshot, report_verify_event, source_hash
    except Exception:
        from cell_structure_observer import verify_cell_count_increased  # type: ignore
        from persistent_notebook_verify import poll_persistent_snapshot, report_verify_event, source_hash  # type: ignore

    before_cells = (before_data or {}).get("cells") if isinstance(before_data, dict) else None
    if before_cells is None:
        before_cells = []

    anchor = int(anchor_index) if anchor_index is not None else None
    direction_norm = str(direction or "below").strip().lower()
    want_index = expected_new_index
    if want_index is None and anchor is not None:
        want_index = anchor + 1 if direction_norm == "below" else anchor
    want_index = int(want_index) if want_index is not None else None

    want_hash = source_hash(expected_content) if expected_content else ""
    last_check: dict[str, Any] = {}

    report_verify_event("insert_watch", url=url, anchor_index=anchor, expected_new_index=want_index)

    def _cell_input(row: dict | None) -> str:
        if not isinstance(row, dict):
            return ""
        return str(row.get("input") or row.get("source") or "")

    def _cell_by_index(cells: list, idx: int) -> dict | None:
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            try:
                if int(cell.get("index")) == int(idx):
                    return cell
            except (TypeError, ValueError):
                continue
        return None

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
        new_row = _cell_by_index(after_cells, want_index) if want_index is not None else None
        if want_index is not None and new_row is None:
            return None
        if want_hash:
            current = _cell_input(new_row)
            if source_hash(current) != want_hash:
                return None
        return {
            "ok": True,
            "insert_verified": True,
            "wait_reason": "persistent_snapshot_structure",
            "anchor_index": anchor,
            "expected_new_index": want_index,
            "new_indices": check.get("new_indices"),
            "count_before": check.get("count_before"),
            "count_after": check.get("count_after"),
            "content_hash": want_hash or None,
        }

    result = poll_persistent_snapshot(url, timeout=timeout, on_tick=_on_tick)
    if result.get("ok"):
        return result

    err = (
        f"insert not verified in persistent JSON within {timeout}s"
        + (f" (expected new cell at index {want_index})" if want_index is not None else "")
    )
    if last_check:
        err += (
            f" — count {last_check.get('count_before')}->{last_check.get('count_after')}"
            f", new_indices={last_check.get('new_indices')}"
        )
    return {
        "ok": False,
        "insert_verified": False,
        "error": err,
        "anchor_index": anchor,
        "expected_new_index": want_index,
        "last_check": last_check or None,
    }
