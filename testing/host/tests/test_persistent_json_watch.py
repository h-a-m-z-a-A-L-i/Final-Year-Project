"""Tests for persistent_json_watch."""

from __future__ import annotations

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.persistent_json_watch import (  # noqa: E402
    _cells_by_index,
    diff_cells,
)


def _cells(rows: list[dict]) -> dict[int, dict]:
    return _cells_by_index({"cells": rows})


def test_uuid_delete_exact_cell():
    before = _cells(
        [
            {"index": 1, "type": "code", "input": "a", "uuid": "u1"},
            {"index": 2, "type": "code", "input": "gone", "uuid": "u2"},
            {"index": 3, "type": "code", "input": "c", "uuid": "u3"},
        ]
    )
    after = _cells(
        [
            {"index": 1, "type": "code", "input": "a", "uuid": "u1"},
            {"index": 2, "type": "code", "input": "c", "uuid": "u3"},
        ]
    )
    events = diff_cells(before, after)
    assert len(events) == 1
    assert events[0] == {"action": "deleted", "index": 2, "uuid": "u2"}


def test_uuid_insert_exact_cell():
    before = _cells([{"index": 1, "type": "code", "input": "a", "uuid": "u1"}])
    after = _cells(
        [
            {"index": 1, "type": "code", "input": "a", "uuid": "u1"},
            {"index": 2, "type": "code", "input": "new", "uuid": "u2"},
        ]
    )
    events = diff_cells(before, after)
    assert events[0] == {"action": "inserted", "index": 2, "uuid": "u2"}


def test_uuid_renumber_only_emits_nothing():
    before = _cells(
        [
            {"index": 1, "type": "code", "input": "a", "uuid": "u1"},
            {"index": 2, "type": "code", "input": "b", "uuid": "u2"},
            {"index": 3, "type": "code", "input": "c", "uuid": "u3"},
        ]
    )
    after = _cells(
        [
            {"index": 10, "type": "code", "input": "a", "uuid": "u1"},
            {"index": 11, "type": "code", "input": "b", "uuid": "u2"},
            {"index": 12, "type": "code", "input": "c", "uuid": "u3"},
        ]
    )
    assert diff_cells(before, after) == []


def test_uuid_batch_insert_below_anchor():
    """Five inserts below index 11 — one diff, five distinct uuids."""
    before = _cells(
        [
            {"index": 11, "type": "code", "input": "anchor", "uuid": "anchor"},
            {"index": 12, "type": "code", "input": "tail", "uuid": "tail"},
        ]
    )
    after = _cells(
        [
            {"index": 11, "type": "code", "input": "anchor", "uuid": "anchor"},
            {"index": 12, "type": "code", "input": "", "uuid": "n1"},
            {"index": 13, "type": "code", "input": "", "uuid": "n2"},
            {"index": 14, "type": "code", "input": "", "uuid": "n3"},
            {"index": 15, "type": "code", "input": "", "uuid": "n4"},
            {"index": 16, "type": "code", "input": "", "uuid": "n5"},
            {"index": 17, "type": "code", "input": "tail", "uuid": "tail"},
        ]
    )
    events = diff_cells(before, after)
    assert [e["action"] for e in events] == ["inserted"] * 5
    assert {e["uuid"] for e in events} == {"n1", "n2", "n3", "n4", "n5"}
    assert [e["index"] for e in events] == [12, 13, 14, 15, 16]


def test_uuid_empty_neighbors_delete():
    before = _cells(
        [
            {"index": 1, "type": "code", "input": "start", "uuid": "s"},
            {"index": 15, "type": "code", "input": "", "uuid": "e15"},
            {"index": 16, "type": "code", "input": "", "uuid": "e16"},
            {"index": 17, "type": "code", "input": "", "uuid": "e17"},
            {"index": 20, "type": "code", "input": "end", "uuid": "end"},
        ]
    )
    after = _cells(
        [
            {"index": 1, "type": "code", "input": "start", "uuid": "s"},
            {"index": 15, "type": "code", "input": "", "uuid": "e15"},
            {"index": 16, "type": "code", "input": "", "uuid": "e16"},
            {"index": 19, "type": "code", "input": "end", "uuid": "end"},
        ]
    )
    events = diff_cells(before, after)
    assert len(events) == 1
    assert events[0]["action"] == "deleted"
    assert events[0]["uuid"] == "e17"
    assert events[0]["index"] == 17


def test_uuid_edit_same_cell():
    before = _cells([{"index": 1, "type": "code", "input": "a", "uuid": "u1"}])
    after = _cells([{"index": 1, "type": "code", "input": "changed", "uuid": "u1"}])
    events = diff_cells(before, after)
    assert events[0]["action"] == "edited"
    assert events[0]["index"] == 1
    assert events[0]["text"] == "changed"
    assert events[0]["uuid"] == "u1"


def test_uuid_double_delete_in_one_update():
    before = _cells(
        [
            {"index": 1, "type": "code", "input": "a", "uuid": "u1"},
            {"index": 2, "type": "code", "input": "gone2", "uuid": "u2"},
            {"index": 3, "type": "code", "input": "gone4", "uuid": "u3"},
            {"index": 4, "type": "code", "input": "keep", "uuid": "u4"},
        ]
    )
    after = _cells(
        [
            {"index": 1, "type": "code", "input": "a", "uuid": "u1"},
            {"index": 2, "type": "code", "input": "keep", "uuid": "u4"},
        ]
    )
    events = diff_cells(before, after)
    assert [e["action"] for e in events] == ["deleted", "deleted"]
    assert {e["uuid"] for e in events} == {"u2", "u3"}


def test_skip_label_prefix_resync_edit():
    before = _cells(
        [{"index": 22, "type": "markdown", "input": "20\nCerebras", "uuid": "md1"}]
    )
    after = _cells(
        [{"index": 22, "type": "markdown", "input": "22\nCerebras", "uuid": "md1"}]
    )
    assert diff_cells(before, after) == []
