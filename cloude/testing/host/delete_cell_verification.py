"""Verify delete_by_index via live notebook snapshot (not extension ack)."""

from __future__ import annotations

import os
import time
from typing import Any

DELETE_VERIFY_TIMEOUT_SEC = float(os.environ.get("DELETE_CELL_VERIFY_TIMEOUT_SEC", "15"))
DELETE_VERIFY_POLL_SEC = float(os.environ.get("DELETE_CELL_VERIFY_POLL_SEC", "0.05"))


def capture_delete_baseline(url: str, cell_index: int) -> dict[str, Any]:
    try:
        from .persistent_notebook_verify import load_persistent_notebook_snapshot
    except Exception:
        from persistent_notebook_verify import load_persistent_notebook_snapshot  # type: ignore

    data, source = load_persistent_notebook_snapshot(url)
    idx = int(cell_index)
    cells = list((data or {}).get("cells") or []) if isinstance(data, dict) else []
    return {
        "url": url,
        "cell_index": idx,
        "snapshot": data if isinstance(data, dict) else {},
        "snapshot_source": source,
        "before_cells": cells,
        "cell_count": len(cells),
    }


def wait_for_delete_verification(
    url: str,
    before_data: dict | None,
    cell_index: int,
    *,
    timeout: float = DELETE_VERIFY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Confirm notebook lost the target cell index in persistent JSON."""
    try:
        from .cell_structure_observer import verify_cell_count_decreased
        from .persistent_notebook_verify import poll_persistent_snapshot, report_verify_event
    except Exception:
        from cell_structure_observer import verify_cell_count_decreased  # type: ignore
        from persistent_notebook_verify import poll_persistent_snapshot, report_verify_event  # type: ignore

    idx = int(cell_index)
    before_cells = (before_data or {}).get("cells") if isinstance(before_data, dict) else None
    if before_cells is None:
        before_cells = []

    last_check: dict[str, Any] = {}
    report_verify_event("delete_watch", url=url, cell_index=idx)

    def _on_tick(data: dict | None, _struct_hash: str, _mtime: float) -> dict[str, Any] | None:
        nonlocal last_check
        after_cells = list((data or {}).get("cells") or []) if isinstance(data, dict) else []
        check = verify_cell_count_decreased(
            before_cells,
            after_cells,
            expected_delta=1,
            expected_indices=[idx],
        )
        last_check = check
        if not check.get("ok"):
            return None
        return {
            "ok": True,
            "delete_verified": True,
            "wait_reason": "persistent_snapshot_structure",
            "cell_index": idx,
            "removed_indices": check.get("removed_indices"),
            "count_before": check.get("count_before"),
            "count_after": check.get("count_after"),
        }

    result = poll_persistent_snapshot(url, timeout=timeout, on_tick=_on_tick)
    if result.get("ok"):
        return result

    err = (
        f"delete not verified in persistent JSON for cell {idx} within {timeout}s"
        f" — count {last_check.get('count_before')}->{last_check.get('count_after')}"
        f", removed_indices={last_check.get('removed_indices')}"
    )
    return {
        "ok": False,
        "delete_verified": False,
        "error": err,
        "cell_index": idx,
        "last_check": last_check or None,
    }
