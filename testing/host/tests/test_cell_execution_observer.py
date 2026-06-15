"""Tests for standalone cell execution observer."""

from __future__ import annotations

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.cell_execution_observer import (  # noqa: E402
    CellExecutionObservation,
    CellExecutionTracker,
    ExecutionEventKind,
    cells_from_raw_execution,
    verify_cell_ran,
)
from testing.host.cell_structure_live import (  # noqa: E402
    merge_execution_observations,
    read_revision_execution_cells,
)


def test_cells_from_raw_execution_parses_metadata():
    raw = [
        {
            "type": "code",
            "index": 3,
            "output": "42",
            "execution_order": 5,
            "execution_title": "Cell executed in 1.2s",
            "execution_status": "executed",
        }
    ]
    cells = cells_from_raw_execution(raw)
    assert len(cells) == 1
    assert cells[0].index == 3
    assert cells[0].execution_order == 5
    assert cells[0].title_executed
    assert cells[0].output_hash


def test_verify_cell_ran_order_increase():
    before = [{"type": "code", "index": 2, "output": "", "execution_order": 1}]
    after = [
        {
            "type": "code",
            "index": 2,
            "output": "hi",
            "execution_order": 2,
            "execution_title": "Cell executed",
        }
    ]
    result = verify_cell_ran(before, after, 2)
    assert result["ok"] is True
    assert result["order_increased"] is True
    assert "order=2" in result["reasons"]


def test_verify_cell_ran_output_only_fallback():
    before = [{"type": "code", "index": 1, "output": ""}]
    after = [{"type": "code", "index": 1, "output": "done"}]
    result = verify_cell_ran(before, after, 1)
    assert result["ok"] is True
    assert result["output_changed"] is True


def test_tracker_emits_running_then_run():
    tracker = CellExecutionTracker(settle_reads=1)
    base = [
        CellExecutionObservation(index=1, execution_order=1, execution_status="idle"),
    ]
    tracker.reset(base)

    running = [
        CellExecutionObservation(
            index=1,
            execution_order=1,
            execution_status="running",
        )
    ]
    ev1 = tracker.observe(running)
    assert any(e.kind == ExecutionEventKind.RUNNING for e in ev1.events)

    done = [
        CellExecutionObservation(
            index=1,
            execution_order=2,
            execution_title="Cell executed",
            execution_status="executed",
            output_hash="abc",
        )
    ]
    ev2 = tracker.observe(done)
    kinds = {e.kind for e in ev2.events}
    assert ExecutionEventKind.RUN in kinds


def test_tracker_debounce_requires_settle_reads():
    tracker = CellExecutionTracker(settle_reads=3)
    tracker.reset([CellExecutionObservation(index=1, execution_order=1)])

    flicker = [CellExecutionObservation(index=1, execution_order=2, output_hash="x")]
    assert not tracker.observe(flicker).events
    assert not tracker.observe(flicker).events
    settled = tracker.observe(flicker)
    assert any(e.kind == ExecutionEventKind.RUN for e in settled.events)


def test_merge_execution_observations_fills_revision_order():
    live = [CellExecutionObservation(index=2, output_hash="out1")]
    revision = [
        CellExecutionObservation(
            index=2,
            execution_order=7,
            execution_title="Cell executed (Execution #7)",
            execution_status="executed",
        )
    ]
    merged = merge_execution_observations(live, revision)
    assert len(merged) == 1
    assert merged[0].execution_order == 7
    assert merged[0].output_hash == "out1"


def test_read_revision_execution_cells_empty_without_file(tmp_path, monkeypatch):
    import testing.host.cell_structure_live as live_mod

    monkeypatch.setattr(live_mod, "_EXECUTION_STATE_PATH", tmp_path / "missing.json")
    assert read_revision_execution_cells("https://example.com/edit") == []


def test_verify_cell_missing_after():
    result = verify_cell_ran([], [], 5)
    assert result["ok"] is False
    assert "not found" in result["error"]
