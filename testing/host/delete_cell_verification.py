"""Verify delete_by_index via persistent JSON + data-uuid."""

from __future__ import annotations

import os
from typing import Any

try:
    from .persistent_notebook_verify import poll_persistent_snapshot, report_verify_event
    from .uuid_cell_diff import verify_delete
except Exception:
    from persistent_notebook_verify import poll_persistent_snapshot, report_verify_event  # type: ignore
    from uuid_cell_diff import verify_delete  # type: ignore

DELETE_VERIFY_TIMEOUT_SEC = float(os.environ.get("DELETE_CELL_VERIFY_TIMEOUT_SEC", "15"))


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
    before_cells = list((before_data or {}).get("cells") or []) if isinstance(before_data, dict) else []
    idx = int(cell_index)
    last_check: dict[str, Any] = {}

    report_verify_event("delete_watch", url=url, cell_index=idx)

    def _on_tick(data: dict | None, _struct_hash: str, _mtime: float) -> dict[str, Any] | None:
        nonlocal last_check
        after_cells = list((data or {}).get("cells") or []) if isinstance(data, dict) else []
        check = verify_delete(before_cells, after_cells, cell_index=idx)
        last_check = check
        if not check.get("ok"):
            return None
        return {
            "ok": True,
            "delete_verified": True,
            "wait_reason": "persistent_uuid_diff",
            "cell_index": idx,
            "uuid": check.get("uuid"),
            "removed_indices": check.get("removed_indices"),
            "count_before": check.get("count_before"),
            "count_after": check.get("count_after"),
        }

    result = poll_persistent_snapshot(url, timeout=timeout, on_tick=_on_tick)
    if result.get("ok"):
        return result

    err = last_check.get("error") or (
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
