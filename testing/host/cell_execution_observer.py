"""Standalone notebook cell execution observation for run detection.

Pure in-memory logic — no persistence, config, or LLM dependencies.
Used by the live terminal monitor and action verification (run_cell).

Observation model:
  - Cells identified by 1-based index (code cells only).
  - Signals: execution_order, execution_title, execution_status, output hash.
  - When metadata is absent (KERNEL_EXECUTION_METADATA_ENABLED=0 in extension),
    falls back to output-hash change as run evidence.
  - Debounced like cell_structure_observer to reject scrape flicker.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class ExecutionEventKind(str, Enum):
    RUNNING = "running"
    RUN = "run"
    DONE = "done"


@dataclass(frozen=True)
class CellExecutionObservation:
    index: int
    execution_order: int | None = None
    execution_title: str = ""
    execution_status: str = "idle"
    output_hash: str = ""

    @property
    def is_running(self) -> bool:
        return str(self.execution_status or "").strip().lower() in ("running", "queued")

    @property
    def title_executed(self) -> bool:
        return _title_is_executed(self.execution_title)


@dataclass(frozen=True)
class CellExecutionEvent:
    kind: ExecutionEventKind
    index: int
    execution_order: int | None = None
    execution_title: str = ""
    execution_status: str = "idle"
    reason: str = ""


@dataclass(frozen=True)
class CellExecutionEvents:
    events: tuple[CellExecutionEvent, ...] = ()


@dataclass
class CellExecutionTracker:
    """Debounced in-memory tracker for cell run / running / done events."""

    settle_reads: int = 2
    committed: dict[int, CellExecutionObservation] = field(default_factory=dict)
    _pending: dict[int, CellExecutionObservation] | None = None
    _stable_reads: int = 0
    _running_emitted: set[int] = field(default_factory=set)

    def reset(self, cells: Iterable[CellExecutionObservation] | None = None) -> None:
        self.committed = execution_index_map(cells or [])
        self._pending = None
        self._stable_reads = 0
        self._running_emitted = {
            idx for idx, c in self.committed.items() if c.is_running
        }

    def observe(self, cells: Iterable[CellExecutionObservation]) -> CellExecutionEvents:
        """Ingest one observation; return execution events when signals settle."""
        current = execution_index_map(cells)
        events: list[CellExecutionEvent] = []

        # RUNNING: emit immediately on first sight (no debounce — brief runs matter).
        for idx, cell in current.items():
            if cell.is_running and idx not in self._running_emitted:
                prev = self.committed.get(idx)
                if prev is None or not prev.is_running:
                    events.append(
                        CellExecutionEvent(
                            kind=ExecutionEventKind.RUNNING,
                            index=idx,
                            execution_order=cell.execution_order,
                            execution_title=cell.execution_title,
                            execution_status=cell.execution_status,
                            reason="status_running",
                        )
                    )
                    self._running_emitted.add(idx)

        for idx, cell in current.items():
            if not cell.is_running:
                self._running_emitted.discard(idx)

        if self._pending is not None and _execution_maps_equal(self._pending, current):
            self._stable_reads += 1
        else:
            self._pending = current
            self._stable_reads = 1

        if self._stable_reads < max(1, int(self.settle_reads)):
            return CellExecutionEvents(events=tuple(events))

        if _execution_maps_equal(self.committed, current):
            return CellExecutionEvents(events=tuple(events))

        settled_events = _diff_execution(self.committed, current)
        events.extend(settled_events)

        self.committed = dict(current)
        self._pending = None
        self._stable_reads = 0
        return CellExecutionEvents(events=tuple(events))


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def _title_is_executed(title: str) -> bool:
    t = str(title or "").strip().lower()
    if not t or "not executed yet" in t:
        return False
    return "cell executed" in t or t.startswith("execution") or "executed" in t


def normalize_raw_execution_cell(cell: dict[str, Any]) -> CellExecutionObservation | None:
    if not isinstance(cell, dict):
        return None
    if str(cell.get("type") or "code").strip().lower() != "code":
        return None
    try:
        idx = int(cell.get("index"))
    except (TypeError, ValueError):
        return None
    if idx < 1:
        return None

    order = cell.get("execution_order")
    try:
        order = int(order) if order is not None else None
    except (TypeError, ValueError):
        order = None

    output = str(cell.get("output") or "")
    return CellExecutionObservation(
        index=idx,
        execution_order=order,
        execution_title=str(cell.get("execution_title") or "").strip(),
        execution_status=str(cell.get("execution_status") or "idle").strip().lower(),
        output_hash=_hash_text(output),
    )


def cells_from_raw_execution(
    raw_cells: Iterable[dict[str, Any]] | None,
) -> list[CellExecutionObservation]:
    out: list[CellExecutionObservation] = []
    for cell in raw_cells or []:
        row = normalize_raw_execution_cell(cell)
        if row is not None:
            out.append(row)
    out.sort(key=lambda c: c.index)
    return out


def execution_index_map(
    cells: Iterable[CellExecutionObservation],
) -> dict[int, CellExecutionObservation]:
    return {cell.index: cell for cell in cells}


def execution_fingerprint(cells: Iterable[CellExecutionObservation]) -> str:
    rows = [
        {
            "index": c.index,
            "execution_order": c.execution_order,
            "execution_title": c.execution_title,
            "execution_status": c.execution_status,
            "output_hash": c.output_hash,
        }
        for c in sorted(cells, key=lambda x: x.index)
    ]
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_cell_ran(
    before_cells: Iterable[dict[str, Any]] | None,
    after_cells: Iterable[dict[str, Any]] | None,
    cell_index: int,
) -> dict[str, Any]:
    """LLM action verification: did a specific cell run between snapshots?"""
    before = execution_index_map(cells_from_raw_execution(before_cells))
    after = execution_index_map(cells_from_raw_execution(after_cells))
    idx = int(cell_index)
    b = before.get(idx)
    a = after.get(idx)
    if a is None:
        return {
            "ok": False,
            "verified": False,
            "cell_index": idx,
            "error": f"cell {idx} not found in after snapshot",
        }

    reasons: list[str] = []
    order_up = False
    if b is not None and a.execution_order is not None:
        if b.execution_order is None or a.execution_order > b.execution_order:
            order_up = True
            reasons.append(f"order={a.execution_order}")
    elif b is None and a.execution_order is not None:
        order_up = True
        reasons.append(f"order={a.execution_order}")

    title_new = bool(a.title_executed and (b is None or a.execution_title != b.execution_title))
    if title_new:
        reasons.append("executed_title")

    output_changed = b is None or a.output_hash != b.output_hash
    if output_changed and a.output_hash:
        reasons.append("output_changed")

    ran = bool(order_up or title_new or (output_changed and a.output_hash))
    return {
        "ok": ran,
        "verified": ran,
        "cell_index": idx,
        "execution_order": a.execution_order,
        "execution_title": a.execution_title,
        "execution_status": a.execution_status,
        "reasons": reasons,
        "output_changed": output_changed,
        "order_increased": order_up,
    }


def _diff_execution(
    before: dict[int, CellExecutionObservation],
    after: dict[int, CellExecutionObservation],
) -> list[CellExecutionEvent]:
    events: list[CellExecutionEvent] = []
    for idx in sorted(set(before) | set(after)):
        prev = before.get(idx)
        cur = after.get(idx)
        if cur is None:
            continue
        if prev is None:
            prev = CellExecutionObservation(index=idx)

        if _is_unstable_execution_flicker(prev, cur):
            continue

        reasons: list[str] = []
        if cur.execution_order is not None and (
            prev.execution_order is None or cur.execution_order > prev.execution_order
        ):
            reasons.append(f"order={cur.execution_order}")

        if cur.title_executed and cur.execution_title != prev.execution_title:
            reasons.append("executed_title")

        if cur.output_hash != prev.output_hash and cur.output_hash:
            reasons.append("output_changed")

        was_running = prev.is_running
        now_running = cur.is_running
        now_done = not now_running and (
            str(cur.execution_status or "").lower() in ("executed", "idle", "")
            or cur.title_executed
        )

        if reasons:
            events.append(
                CellExecutionEvent(
                    kind=ExecutionEventKind.RUN,
                    index=idx,
                    execution_order=cur.execution_order,
                    execution_title=cur.execution_title,
                    execution_status=cur.execution_status,
                    reason=",".join(reasons),
                )
            )

        if was_running and now_done and not now_running:
            events.append(
                CellExecutionEvent(
                    kind=ExecutionEventKind.DONE,
                    index=idx,
                    execution_order=cur.execution_order,
                    execution_title=cur.execution_title,
                    execution_status=cur.execution_status,
                    reason="running_finished",
                )
            )

    return events


def _is_unstable_execution_flicker(
    before: CellExecutionObservation,
    after: CellExecutionObservation,
) -> bool:
    """Reject output-only flicker with no order/title/status movement."""
    if before.output_hash == after.output_hash:
        return False
    if after.execution_order != before.execution_order:
        return False
    if after.execution_title != before.execution_title:
        return False
    if after.is_running or before.is_running:
        return False
    if after.title_executed != before.title_executed:
        return False
    # Output changed but nothing else — accept (valid run signal without metadata).
    return False


def _execution_maps_equal(
    a: dict[int, CellExecutionObservation],
    b: dict[int, CellExecutionObservation],
) -> bool:
    if set(a) != set(b):
        return False
    for idx in a:
        left, right = a[idx], b[idx]
        if (
            left.execution_order != right.execution_order
            or left.execution_title != right.execution_title
            or left.execution_status != right.execution_status
            or left.output_hash != right.output_hash
        ):
            return False
    return True
