"""Verify insert_cell via persistent JSON + data-uuid."""

from __future__ import annotations

import os
from typing import Any

try:
    from .persistent_notebook_verify import poll_persistent_snapshot, report_verify_event
    from .uuid_cell_diff import verify_insert
except Exception:
    from persistent_notebook_verify import poll_persistent_snapshot, report_verify_event  # type: ignore
    from uuid_cell_diff import verify_insert  # type: ignore

INSERT_VERIFY_TIMEOUT_SEC = float(os.environ.get("INSERT_CELL_VERIFY_TIMEOUT_SEC", "15"))


def capture_insert_baseline(url: str, anchor_index: int, *, direction: str = "below") -> dict[str, Any]:
    try:
        from .persistent_notebook_verify import load_persistent_notebook_snapshot
    except Exception:
        from persistent_notebook_verify import load_persistent_notebook_snapshot  # type: ignore

    data, source = load_persistent_notebook_snapshot(url)
    anchor = int(anchor_index)
    direction_norm = str(direction or "below").strip().lower()
    expected_new_index = anchor + 1 if direction_norm == "below" else anchor
    cells = list((data or {}).get("cells") or []) if isinstance(data, dict) else []
    return {
        "url": url,
        "anchor_index": anchor,
        "direction": direction_norm,
        "expected_new_index": expected_new_index,
        "snapshot": data if isinstance(data, dict) else {},
        "snapshot_source": source,
        "before_cells": cells,
        "cell_count": len(cells),
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
    before_cells = list((before_data or {}).get("cells") or []) if isinstance(before_data, dict) else []

    anchor = int(anchor_index) if anchor_index is not None else None
    direction_norm = str(direction or "below").strip().lower()
    want_index = expected_new_index
    if want_index is None and anchor is not None:
        want_index = anchor + 1 if direction_norm == "below" else anchor
    want_index = int(want_index) if want_index is not None else None

    last_check: dict[str, Any] = {}
    report_verify_event("insert_watch", url=url, anchor_index=anchor, expected_new_index=want_index)

    def _on_tick(data: dict | None, _struct_hash: str, _mtime: float) -> dict[str, Any] | None:
        nonlocal last_check
        after_cells = list((data or {}).get("cells") or []) if isinstance(data, dict) else []
        check = verify_insert(
            before_cells,
            after_cells,
            expected_index=want_index,
            expected_delta=1,
            expected_content=str(expected_content or ""),
        )
        last_check = check
        if not check.get("ok"):
            return None
        return {
            "ok": True,
            "insert_verified": True,
            "wait_reason": "persistent_uuid_diff",
            "anchor_index": anchor,
            "expected_new_index": want_index,
            "new_indices": check.get("new_indices"),
            "inserted": check.get("inserted"),
            "count_before": check.get("count_before"),
            "count_after": check.get("count_after"),
        }

    result = poll_persistent_snapshot(url, timeout=timeout, on_tick=_on_tick)
    if result.get("ok"):
        return result

    err = f"insert not verified in persistent JSON within {timeout}s"
    if want_index is not None:
        err += f" (expected new cell at index {want_index})"
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
