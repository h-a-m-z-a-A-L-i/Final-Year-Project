"""Standalone notebook cell-structure observation for action verification.



Pure in-memory logic — no persistence, registry, or chat pipeline dependencies.

Used by agentic verification (insert/delete) and the terminal monitor script.



Observation model:

  - Cells are identified by 1-based index + type + input body (Kaggle scrape).

  - Kaggle/Jupyter expose *positional* indices (1..N) that renumber on middle

    insert/delete — a naive set diff on index keys only ever sees the last index

    change. We align ordered cell sequences by (type, input) to find real slots.

  - A *new cell* is detected when count increases and sequence alignment finds

    inserted slot(s); reported index comes from the *after* snapshot.

  - A *deleted cell* is detected when count decreases and alignment finds removed

    slot(s); reported index comes from the *before* snapshot.

  - Identical empty (or duplicate-content) cells may align ambiguously.

"""



from __future__ import annotations



import difflib

import hashlib

import json

from dataclasses import dataclass, field

from typing import Any, Iterable





@dataclass(frozen=True)

class CellObservation:

    index: int

    cell_type: str

    input: str



    @property

    def is_empty(self) -> bool:

        return not str(self.input or "").strip()





@dataclass(frozen=True)

class CellStructureDiff:

    count_before: int

    count_after: int

    new_indices: tuple[int, ...] = ()

    removed_indices: tuple[int, ...] = ()

    count_delta: int = 0



    def __post_init__(self) -> None:

        object.__setattr__(self, "count_delta", self.count_after - self.count_before)





@dataclass(frozen=True)

class CellAdditionEvent:

    """One verified new cell (emitted after structure settles)."""



    index: int

    cell_type: str

    input_preview: str

    total_cells: int

    reason: str = "new_index"





@dataclass(frozen=True)

class CellDeletionEvent:

    """One verified removed cell (emitted after structure settles)."""



    index: int

    cell_type: str

    input_preview: str

    total_cells: int

    reason: str = "removed_index"





@dataclass(frozen=True)

class CellStructureEvents:

    additions: tuple[CellAdditionEvent, ...] = ()

    deletions: tuple[CellDeletionEvent, ...] = ()





@dataclass

class CellStructureTracker:

    """Debounced in-memory tracker — ignores scrape flicker and content-only edits."""



    settle_reads: int = 3

    committed: dict[int, CellObservation] = field(default_factory=dict)

    _pending: dict[int, CellObservation] | None = None

    _stable_reads: int = 0



    @property

    def committed_count(self) -> int:

        return len(self.committed)



    @property

    def committed_indices(self) -> set[int]:

        return set(self.committed)



    def reset(self, cells: Iterable[CellObservation] | None = None) -> None:

        self.committed = index_map(cells or [])

        self._pending = None

        self._stable_reads = 0



    def observe(self, cells: Iterable[CellObservation]) -> CellStructureEvents:

        """Ingest one observation; return structure events when settled."""

        current = index_map(cells)

        if self._pending is not None and _maps_equal(self._pending, current):

            self._stable_reads += 1

        else:

            self._pending = current

            self._stable_reads = 1



        if self._stable_reads < max(1, int(self.settle_reads)):

            return CellStructureEvents()



        if _maps_equal(self.committed, current):

            return CellStructureEvents()



        diff = diff_structures(self.committed, current)

        if _is_unstable_structure_change(self.committed, current, diff):

            self._stable_reads = 0

            return CellStructureEvents()



        tail_drop = _is_tail_drop_false_removal(self.committed, diff)

        additions: list[CellAdditionEvent] = []

        deletions: list[CellDeletionEvent] = []



        effective_delta = diff.count_delta

        if tail_drop:

            effective_delta = len(diff.new_indices)



        if effective_delta > 0 and diff.new_indices:

            for idx in diff.new_indices:

                cell = current[idx]

                additions.append(

                    CellAdditionEvent(

                        index=idx,

                        cell_type=cell.cell_type,

                        input_preview=input_preview(cell.input),

                        total_cells=diff.count_after,

                        reason="new_index",

                    )

                )



        if diff.count_delta < 0 and diff.removed_indices and not tail_drop:

            for idx in diff.removed_indices:

                cell = self.committed[idx]

                deletions.append(

                    CellDeletionEvent(

                        index=idx,

                        cell_type=cell.cell_type,

                        input_preview=input_preview(cell.input),

                        total_cells=diff.count_after,

                        reason="removed_index",

                    )

                )



        self.committed = dict(current)

        self._pending = None

        self._stable_reads = 0

        return CellStructureEvents(

            additions=tuple(additions),

            deletions=tuple(deletions),

        )





def normalize_raw_cell(cell: dict[str, Any]) -> CellObservation | None:

    if not isinstance(cell, dict):

        return None

    try:

        idx = int(cell.get("index"))

    except (TypeError, ValueError):

        return None

    if idx < 1:

        return None

    ctype = str(cell.get("type") or "code").strip().lower()

    body = str(cell.get("input") or cell.get("source") or "")

    return CellObservation(index=idx, cell_type=ctype, input=body)





def cells_from_raw(raw_cells: Iterable[dict[str, Any]] | None) -> list[CellObservation]:

    out: list[CellObservation] = []

    for cell in raw_cells or []:

        row = normalize_raw_cell(cell)

        if row is not None:

            out.append(row)

    out.sort(key=lambda c: c.index)

    return out





def index_map(cells: Iterable[CellObservation]) -> dict[int, CellObservation]:

    return {cell.index: cell for cell in cells}





def structure_fingerprint(cells: Iterable[CellObservation]) -> str:

    rows = [

        {"index": c.index, "type": c.cell_type, "input": c.input}

        for c in sorted(cells, key=lambda x: x.index)

    ]

    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()





def _markdown_body_for_alignment(input_text: str) -> str:

    """Strip leading section-number line Kaggle rewrites when cells renumber."""

    text = str(input_text or "")

    if not text:

        return ""

    lines = text.split("\n", 1)

    first = lines[0].strip()

    if first.isdigit():

        return lines[1] if len(lines) > 1 else ""

    return text





def _cell_content_key(cell: CellObservation) -> str:

    """Stable alignment key — type + body (indices shift on Kaggle renumber)."""

    body = cell.input

    if cell.cell_type == "markdown":

        body = _markdown_body_for_alignment(body)

    return f"{cell.cell_type}\0{body}"





def _ordered_cells(cells: dict[int, CellObservation]) -> list[CellObservation]:

    return [cells[i] for i in sorted(cells)]





def diff_structures(

    before: dict[int, CellObservation],

    after: dict[int, CellObservation],

) -> CellStructureDiff:

    """Diff by ordered content alignment, not raw index-set comparison."""

    before_list = _ordered_cells(before)

    after_list = _ordered_cells(after)

    before_keys = [_cell_content_key(c) for c in before_list]

    after_keys = [_cell_content_key(c) for c in after_list]



    new_indices: list[int] = []

    removed_indices: list[int] = []



    matcher = difflib.SequenceMatcher(None, before_keys, after_keys, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():

        if tag == "insert":

            for j in range(j1, j2):

                new_indices.append(after_list[j].index)

        elif tag == "delete":

            for i in range(i1, i2):

                removed_indices.append(before_list[i].index)

        elif tag == "replace":

            for i in range(i1, i2):

                removed_indices.append(before_list[i].index)

            for j in range(j1, j2):

                new_indices.append(after_list[j].index)



    return CellStructureDiff(

        count_before=len(before),

        count_after=len(after),

        new_indices=tuple(new_indices),

        removed_indices=tuple(removed_indices),

    )





def verify_cell_count_increased(

    before_cells: Iterable[dict[str, Any]] | None,

    after_cells: Iterable[dict[str, Any]] | None,

    *,

    expected_delta: int = 1,

    expected_indices: Iterable[int] | None = None,

) -> dict[str, Any]:

    """LLM action verification: did the notebook gain cell(s) after an insert?"""

    before = index_map(cells_from_raw(before_cells))

    after = index_map(cells_from_raw(after_cells))

    diff = diff_structures(before, after)

    ok = diff.count_delta >= int(expected_delta)

    missing: list[int] = []

    if expected_indices is not None:

        for idx in expected_indices:

            if int(idx) not in diff.new_indices:

                missing.append(int(idx))

        if missing:

            ok = False

    return {

        "ok": ok,

        "verified": ok,

        "count_before": diff.count_before,

        "count_after": diff.count_after,

        "count_delta": diff.count_delta,

        "new_indices": list(diff.new_indices),

        "removed_indices": list(diff.removed_indices),

        "missing_expected_indices": missing,

        "expected_delta": int(expected_delta),

    }





def verify_cell_count_decreased(

    before_cells: Iterable[dict[str, Any]] | None,

    after_cells: Iterable[dict[str, Any]] | None,

    *,

    expected_delta: int = 1,

    expected_indices: Iterable[int] | None = None,

) -> dict[str, Any]:

    """LLM action verification: did the notebook lose cell(s) after a delete?"""

    before = index_map(cells_from_raw(before_cells))

    after = index_map(cells_from_raw(after_cells))

    diff = diff_structures(before, after)

    ok = diff.count_delta <= -int(expected_delta)

    missing: list[int] = []

    if expected_indices is not None:

        for idx in expected_indices:

            if int(idx) not in diff.removed_indices:

                missing.append(int(idx))

        if missing:

            ok = False

    return {

        "ok": ok,

        "verified": ok,

        "count_before": diff.count_before,

        "count_after": diff.count_after,

        "count_delta": diff.count_delta,

        "new_indices": list(diff.new_indices),

        "removed_indices": list(diff.removed_indices),

        "missing_expected_indices": missing,

        "expected_delta": int(expected_delta),

    }





def verify_cell_at_index(

    cells: Iterable[dict[str, Any]] | None,

    cell_index: int,

    *,

    content_contains: str | None = None,

    cell_type: str | None = None,

) -> dict[str, Any]:

    """LLM action verification: does index exist with optional content/type checks?"""

    mapped = index_map(cells_from_raw(cells))

    cell = mapped.get(int(cell_index))

    if cell is None:

        return {

            "ok": False,

            "verified": False,

            "cell_index": int(cell_index),

            "error": f"cell {cell_index} not found",

        }

    ok = True

    if cell_type is not None and cell.cell_type != str(cell_type).strip().lower():

        ok = False

    if content_contains is not None and content_contains not in cell.input:

        ok = False

    return {

        "ok": ok,

        "verified": ok,

        "cell_index": int(cell_index),

        "cell_type": cell.cell_type,

        "input_preview": input_preview(cell.input),

        "content_match": content_contains is None or content_contains in cell.input,

    }





def input_preview(text: str, *, max_len: int = 72) -> str:

    raw = str(text or "").strip().replace("\n", "\\n")

    if not raw:

        return "(empty)"

    if len(raw) > max_len:

        return raw[: max_len - 1] + "…"

    return raw





def _is_tail_drop_false_removal(
    before: dict[int, CellObservation],
    diff: CellStructureDiff,
) -> bool:
    """Middle insert can look like insert+delete when scrape omits the trailing cell."""
    if diff.count_delta != 0:
        return False
    if len(diff.new_indices) != 1 or len(diff.removed_indices) != 1:
        return False
    if diff.new_indices[0] == diff.removed_indices[0]:
        return False
    if not before:
        return False
    return diff.removed_indices[0] == max(before)


def _is_unstable_structure_change(
    before: dict[int, CellObservation],
    after: dict[int, CellObservation],
    diff: CellStructureDiff,
) -> bool:
    """Reject partial scrapes and spurious renumber churn before committing."""
    n_before = len(before)
    n_after = len(after)
    max_n = max(n_before, n_after, 1)
    n_added = len(diff.new_indices)
    n_removed = len(diff.removed_indices)
    delta = diff.count_delta

    if delta < -1:
        return True

    if delta > 0 and n_added != delta:
        return True
    if delta < 0 and n_removed != abs(delta):
        return True

    if delta == 0 and (n_added or n_removed):
        if _is_tail_drop_false_removal(before, diff):
            return False
        return True

    if (n_added + n_removed) / max_n > 0.5:
        return True

    return False


def _maps_equal(a: dict[int, CellObservation], b: dict[int, CellObservation]) -> bool:

    if set(a) != set(b):

        return False

    for idx in a:

        left, right = a[idx], b[idx]

        if left.cell_type != right.cell_type or left.input != right.input:

            return False

    return True


