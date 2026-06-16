"""Tests for standalone cell structure observer (verification)."""



from __future__ import annotations



import os

import sys



repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

if repo_root not in sys.path:

    sys.path.insert(0, repo_root)



from testing.host.cell_structure_observer import (  # noqa: E402

    CellObservation,

    CellStructureDiff,

    CellStructureTracker,

    _is_unstable_structure_change,

    cells_from_raw,

    diff_structures,

    index_map,

    verify_cell_at_index,

    verify_cell_count_decreased,

    verify_cell_count_increased,

)





def test_diff_detects_new_index_on_count_increase():

    before = index_map([CellObservation(1, "code", "a"), CellObservation(2, "code", "b")])

    after = index_map(

        [

            CellObservation(1, "code", "a"),

            CellObservation(2, "code", "b"),

            CellObservation(3, "code", "c"),

        ]

    )

    diff = diff_structures(before, after)

    assert diff.count_delta == 1

    assert diff.new_indices == (3,)





def test_diff_detects_insert_at_middle_when_indices_renumber():

    """Kaggle renumbers 1..N — set diff would only see index 4; alignment finds slot 2."""

    before = index_map(

        [

            CellObservation(1, "code", "a"),

            CellObservation(2, "code", "b"),

            CellObservation(3, "code", "c"),

        ]

    )

    after = index_map(

        [

            CellObservation(1, "code", "a"),

            CellObservation(2, "code", "NEW"),

            CellObservation(3, "code", "b"),

            CellObservation(4, "code", "c"),

        ]

    )

    diff = diff_structures(before, after)

    assert diff.count_delta == 1

    assert diff.new_indices == (2,)

    assert diff.removed_indices == ()





def test_diff_detects_removed_index_on_count_decrease():

    before = index_map(

        [

            CellObservation(1, "code", "a"),

            CellObservation(2, "code", "b"),

            CellObservation(3, "code", "c"),

        ]

    )

    after = index_map([CellObservation(1, "code", "a"), CellObservation(2, "code", "c")])

    diff = diff_structures(before, after)

    assert diff.count_delta == -1

    assert diff.removed_indices == (2,)

    assert diff.new_indices == ()





def test_content_edit_does_not_increase_count():

    before = [{"type": "code", "index": 1, "input": ""}]

    after = [{"type": "code", "index": 1, "input": "print(1)"}]

    result = verify_cell_count_increased(before, after, expected_delta=1)

    assert result["ok"] is False

    assert result["count_delta"] == 0





def test_verify_insert_ok():

    before = [{"type": "code", "index": 1, "input": "x"}]

    after = [

        {"type": "code", "index": 1, "input": "x"},

        {"type": "code", "index": 2, "input": "y"},

    ]

    result = verify_cell_count_increased(before, after, expected_delta=1, expected_indices=[2])

    assert result["ok"] is True

    assert result["new_indices"] == [2]





def test_verify_delete_ok():

    before = [

        {"type": "code", "index": 1, "input": "x"},

        {"type": "code", "index": 2, "input": "y"},

    ]

    after = [{"type": "code", "index": 1, "input": "x"}]

    result = verify_cell_count_decreased(before, after, expected_delta=1, expected_indices=[2])

    assert result["ok"] is True

    assert result["removed_indices"] == [2]





def test_tracker_ignores_content_only_churn():

    tracker = CellStructureTracker(settle_reads=1)

    tracker.reset(cells_from_raw([{"type": "code", "index": 1, "input": ""}]))

    events = tracker.observe(cells_from_raw([{"type": "code", "index": 1, "input": "print(1)"}]))

    assert events.additions == ()

    assert events.deletions == ()





def test_tracker_emits_on_new_index_after_settle():

    tracker = CellStructureTracker(settle_reads=2)

    base = [{"type": "code", "index": 1, "input": "a"}]

    tracker.reset(cells_from_raw(base))

    added = [

        {"type": "code", "index": 1, "input": "a"},

        {"type": "code", "index": 2, "input": "b"},

    ]

    assert tracker.observe(cells_from_raw(added)).additions == ()

    events = tracker.observe(cells_from_raw(added))

    assert len(events.additions) == 1

    assert events.additions[0].index == 2

    assert events.additions[0].total_cells == 2





def test_tracker_emits_middle_insert_after_settle():

    tracker = CellStructureTracker(settle_reads=2)

    base = [

        {"type": "code", "index": 1, "input": "a"},

        {"type": "code", "index": 2, "input": "b"},

        {"type": "code", "index": 3, "input": "c"},

    ]

    tracker.reset(cells_from_raw(base))

    inserted = [

        {"type": "code", "index": 1, "input": "a"},

        {"type": "code", "index": 2, "input": "NEW"},

        {"type": "code", "index": 3, "input": "b"},

        {"type": "code", "index": 4, "input": "c"},

    ]

    assert tracker.observe(cells_from_raw(inserted)).additions == ()

    events = tracker.observe(cells_from_raw(inserted))

    assert len(events.additions) == 1

    assert events.additions[0].index == 2

    assert events.additions[0].total_cells == 4





def test_tracker_emits_on_delete_after_settle():

    tracker = CellStructureTracker(settle_reads=2)

    base = [

        {"type": "code", "index": 1, "input": "a"},

        {"type": "code", "index": 2, "input": "b"},

        {"type": "code", "index": 3, "input": "c"},

    ]

    tracker.reset(cells_from_raw(base))

    removed = [

        {"type": "code", "index": 1, "input": "a"},

        {"type": "code", "index": 2, "input": "c"},

    ]

    assert tracker.observe(cells_from_raw(removed)).deletions == ()

    events = tracker.observe(cells_from_raw(removed))

    assert len(events.deletions) == 1

    assert events.deletions[0].index == 2

    assert events.deletions[0].total_cells == 2





def test_verify_cell_at_index():

    cells = [{"type": "markdown", "index": 3, "input": "# Title"}]

    ok = verify_cell_at_index(cells, 3, cell_type="markdown", content_contains="Title")

    assert ok["ok"] is True

    bad = verify_cell_at_index(cells, 99)

    assert bad["ok"] is False





def _notebook_with_numbered_markdown(count: int, *, markdown_at: int) -> list[CellObservation]:

    cells: list[CellObservation] = []

    for idx in range(1, count + 1):

        if idx == markdown_at:

            cells.append(CellObservation(idx, "markdown", f"{idx}\nCerebras"))

        else:

            cells.append(CellObservation(idx, "code", f"code_{idx}"))

    return cells





def test_diff_insert_code_ignores_markdown_section_renumber():

    """Inserting code must not look like markdown delete+add when only the header number shifts."""

    before = _notebook_with_numbered_markdown(19, markdown_at=17)

    after: list[CellObservation] = [CellObservation(1, "code", "code_1"), CellObservation(2, "code", "")]

    for idx in range(3, 21):

        if idx == 18:

            after.append(CellObservation(18, "markdown", "18\nCerebras"))

        else:

            after.append(CellObservation(idx, "code", f"code_{idx - 1}"))

    diff = diff_structures(index_map(before), index_map(after))

    assert diff.count_delta == 1

    assert diff.new_indices == (2,)

    assert diff.removed_indices == ()

    assert _is_unstable_structure_change(index_map(before), index_map(after), diff) is False





def test_tracker_single_insert_no_markdown_false_events():

    tracker = CellStructureTracker(settle_reads=2)

    before = _notebook_with_numbered_markdown(5, markdown_at=4)

    after = [

        CellObservation(1, "code", "code_1"),

        CellObservation(2, "code", ""),

        CellObservation(3, "code", "code_2"),

        CellObservation(4, "code", "code_3"),

        CellObservation(5, "markdown", "5\nCerebras"),

        CellObservation(6, "code", "code_5"),

    ]

    tracker.reset(before)

    assert tracker.observe(after).additions == ()

    events = tracker.observe(after)

    assert len(events.additions) == 1

    assert events.additions[0].index == 2

    assert events.additions[0].cell_type == "code"

    assert events.deletions == ()





def test_tracker_rejects_partial_scrape_burst():

    tracker = CellStructureTracker(settle_reads=2)

    base = _notebook_with_numbered_markdown(6, markdown_at=5)

    tracker.reset(base)

    partial = base[:3]

    assert tracker.observe(partial).deletions == ()

    assert tracker.observe(partial).deletions == ()

    recovered = [

        CellObservation(1, "code", "code_1"),

        CellObservation(2, "code", ""),

        CellObservation(3, "code", "code_2"),

        CellObservation(4, "code", "code_3"),

        CellObservation(5, "code", "code_4"),

        CellObservation(6, "markdown", "6\nCerebras"),

        CellObservation(7, "code", "code_6"),

    ]

    assert tracker.observe(recovered).additions == ()

    events = tracker.observe(recovered)

    assert len(events.additions) == 1

    assert events.additions[0].index == 2

    assert events.deletions == ()





def test_unstable_structure_change_flags_spurious_churn():

    before = index_map(_notebook_with_numbered_markdown(5, markdown_at=4))

    after = index_map(_notebook_with_numbered_markdown(5, markdown_at=4))

    spurious = CellStructureDiff(

        count_before=5,

        count_after=5,

        new_indices=(4, 5),

        removed_indices=(4,),

    )

    assert _is_unstable_structure_change(before, after, spurious) is True

    clean = diff_structures(before, after)

    assert _is_unstable_structure_change(before, after, clean) is False


