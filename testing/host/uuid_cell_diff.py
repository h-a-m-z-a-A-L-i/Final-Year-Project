"""UUID-keyed notebook cell structural diff — single source of truth."""

from __future__ import annotations

import re
from typing import Any, Iterable

try:
    from .persistent_notebook_verify import normalize_cell_source, source_hash
except Exception:
    from persistent_notebook_verify import normalize_cell_source, source_hash  # type: ignore


def cell_uuid(cell: dict) -> str:
    raw = cell.get("uuid") or cell.get("data_uuid") or cell.get("data-uuid")
    return str(raw or "").strip()


def cell_content(cell: dict) -> str:
    return str(cell.get("input") or cell.get("source") or "")


def ordered_cells(cells: Iterable[dict]) -> list[dict]:
    rows = [c for c in cells if isinstance(c, dict)]
    rows.sort(key=lambda c: int(c.get("index") or 0))
    return rows


def cells_by_uuid(cells: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cell in ordered_cells(cells):
        uid = cell_uuid(cell)
        if uid:
            out[uid] = cell
    return out


def cell_at_index(cells: Iterable[dict], index: int) -> dict | None:
    want = int(index)
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        try:
            if int(cell.get("index")) == want:
                return cell
        except (TypeError, ValueError):
            continue
    return None


def uuid_coverage(cells: Iterable[dict]) -> float:
    rows = ordered_cells(cells)
    if not rows:
        return 0.0
    return sum(1 for c in rows if cell_uuid(c)) / len(rows)


def _label_prefix_only_change(before: str, after: str) -> bool:
    b = normalize_cell_source(before)
    a = normalize_cell_source(after)
    if b == a:
        return True
    if not (re.match(r"^\d+\n", b) and re.match(r"^\d+\n", a)):
        return False
    return b.split("\n", 1)[1] == a.split("\n", 1)[1]


def diff_cells(before: Iterable[dict], after: Iterable[dict]) -> list[dict[str, Any]]:
    """Detect insert / delete / edit by stable data-uuid."""
    before_list = ordered_cells(before)
    after_list = ordered_cells(after)
    before_map = cells_by_uuid(before_list)
    after_map = cells_by_uuid(after_list)

    events: list[dict[str, Any]] = []

    for uid, cell in before_map.items():
        if uid not in after_map:
            events.append(
                {
                    "action": "deleted",
                    "index": int(cell.get("index") or 0),
                    "uuid": uid,
                }
            )

    for uid, cell in after_map.items():
        if uid not in before_map:
            events.append(
                {
                    "action": "inserted",
                    "index": int(cell.get("index") or 0),
                    "uuid": uid,
                }
            )

    if len(before_list) == len(after_list) and not events:
        for uid in before_map.keys() & after_map.keys():
            prev = before_map[uid]
            cur = after_map[uid]
            if source_hash(cell_content(prev)) == source_hash(cell_content(cur)):
                continue
            if _label_prefix_only_change(cell_content(prev), cell_content(cur)):
                continue
            events.append(
                {
                    "action": "edited",
                    "index": int(cur.get("index") or 0),
                    "uuid": uid,
                    "text": normalize_cell_source(cell_content(cur)),
                }
            )

    return events


def verify_insert(
    before: Iterable[dict],
    after: Iterable[dict],
    *,
    expected_index: int | None = None,
    expected_delta: int = 1,
    expected_content: str = "",
) -> dict[str, Any]:
    before_list = ordered_cells(before)
    after_list = ordered_cells(after)
    inserts = [e for e in diff_cells(before_list, after_list) if e["action"] == "inserted"]
    ok = len(inserts) >= int(expected_delta)
    missing_index: list[int] = []

    if expected_index is not None:
        want = int(expected_index)
        if not any(int(e["index"]) == want for e in inserts):
            missing_index.append(want)
            ok = False

    if ok and expected_content and expected_index is not None:
        row = cell_at_index(after_list, int(expected_index))
        if row is None or source_hash(cell_content(row)) != source_hash(expected_content):
            ok = False

    return {
        "ok": ok,
        "verified": ok,
        "count_before": len(before_list),
        "count_after": len(after_list),
        "count_delta": len(after_list) - len(before_list),
        "inserted": inserts,
        "new_indices": [int(e["index"]) for e in inserts],
        "missing_expected_indices": missing_index,
        "expected_delta": int(expected_delta),
    }


def verify_delete(
    before: Iterable[dict],
    after: Iterable[dict],
    *,
    cell_index: int,
) -> dict[str, Any]:
    before_list = ordered_cells(before)
    after_list = ordered_cells(after)
    idx = int(cell_index)
    target = cell_at_index(before_list, idx)
    if target is None:
        return {
            "ok": False,
            "verified": False,
            "cell_index": idx,
            "error": f"cell {idx} not found in before snapshot",
        }

    uid = cell_uuid(target)
    if not uid:
        return {
            "ok": False,
            "verified": False,
            "cell_index": idx,
            "error": f"cell {idx} has no uuid — reload extension and refresh notebook",
        }

    after_map = cells_by_uuid(after_list)
    ok = uid not in after_map
    deletions = [e for e in diff_cells(before_list, after_list) if e["action"] == "deleted"]

    return {
        "ok": ok,
        "verified": ok,
        "cell_index": idx,
        "uuid": uid,
        "count_before": len(before_list),
        "count_after": len(after_list),
        "count_delta": len(after_list) - len(before_list),
        "removed_indices": [int(e["index"]) for e in deletions if e.get("uuid") == uid],
        "deleted": deletions,
    }
