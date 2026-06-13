"""Verification layer for persistent notebook JSON updates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PersistentUpdateDecision:
    allow_write: bool
    changed: bool
    reason: str


def _cell_kind(cell: dict) -> str:
    return str(cell.get("type") or "code").strip().lower()


def normalize_cell_for_compare(cell: dict) -> dict:
    """Normalize a cell to comparable fields used for persistent snapshots."""
    ctype = _cell_kind(cell)
    try:
        idx = int(cell.get("index", 0))
    except (TypeError, ValueError):
        idx = 0

    normalized: dict[str, Any] = {
        "type": ctype,
        "index": idx,
        "input": str(cell.get("input") or cell.get("source") or "").strip(),
    }
    if ctype == "markdown":
        normalized["state"] = str(cell.get("state") or "open").strip()
        return normalized

    normalized["output"] = str(cell.get("output") or "").strip()
    order = cell.get("execution_order")
    normalized["execution_order"] = None if order is None else str(order)
    normalized["execution_title"] = str(cell.get("execution_title") or "").strip()
    return normalized


def cells_from_snapshot(data: dict | None) -> list[dict]:
    if not isinstance(data, dict):
        return []
    raw_cells = data.get("cells")
    if not isinstance(raw_cells, list):
        return []

    normalized: list[dict] = []
    for cell in raw_cells:
        if isinstance(cell, dict) and cell.get("index") is not None:
            normalized.append(normalize_cell_for_compare(cell))
    normalized.sort(key=lambda item: int(item.get("index", 0)))
    return normalized


def count_code_cells(cells: list[dict]) -> int:
    return sum(1 for cell in cells if cell.get("type") != "markdown")


def snapshot_fingerprint(cells: list[dict]) -> str:
    payload = json.dumps(cells, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def apply_execution_metadata_clear(data: dict) -> dict:
    """Return a copy with execution metadata cleared (fresh kernel session)."""
    cleared = dict(data)
    cells: list[dict] = []
    for raw in list(cleared.get("cells") or []):
        if not isinstance(raw, dict):
            continue
        cell = dict(raw)
        cell["execution_order"] = None
        cell["execution_title"] = ""
        cell.pop("execution_timestamp", None)
        cells.append(cell)
    cleared["cells"] = cells
    return cleared


def evaluate_persistent_update(existing: dict | None, incoming: dict) -> PersistentUpdateDecision:
    """
    Decide whether a persistent snapshot should be written.

    Safety rules:
    - Never replace a populated snapshot with an empty one.
    - Never accept partial scrapes with fewer code cells than the stored snapshot.
    - Only write when normalized notebook content actually changed.
    """
    incoming_cells = cells_from_snapshot(incoming)
    existing_cells = cells_from_snapshot(existing)

    incoming_code = count_code_cells(incoming_cells)
    existing_code = count_code_cells(existing_cells)

    if not existing_cells:
        if not incoming_cells:
            return PersistentUpdateDecision(False, False, "no_cells")
        return PersistentUpdateDecision(True, True, "initial_create")

    if incoming_code == 0 and existing_code > 0:
        return PersistentUpdateDecision(False, False, "reject_empty_incoming")

    if incoming_code < existing_code:
        return PersistentUpdateDecision(
            False,
            False,
            f"reject_partial_scrape ({incoming_code}<{existing_code})",
        )

    if snapshot_fingerprint(existing_cells) == snapshot_fingerprint(incoming_cells):
        return PersistentUpdateDecision(False, False, "unchanged")

    return PersistentUpdateDecision(True, True, "content_changed")
