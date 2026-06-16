"""Tests for standalone cell execution observer."""

from __future__ import annotations

import json
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
    read_host_log_exec_lines,
    read_kernel_scenario_for_url,
    read_live_code_outputs,
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


def test_read_host_log_exec_lines_parses_and_seeks(tmp_path, monkeypatch):
    import testing.host.cell_structure_live as live_mod

    log_path = tmp_path / "host.log"
    log_path.write_text(
        "[17:38:28] EXEC DETECTED cell=12 order=60\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(live_mod, "_HOST_LOG_PATH", log_path)

    pairs, pos = read_host_log_exec_lines(0)
    assert pairs == [(12, 60)]
    assert pos == log_path.stat().st_size

    log_path.write_text(
        log_path.read_text(encoding="utf-8")
        + "[17:38:29] EXEC DETECTED cell=3 order=2\n",
        encoding="utf-8",
    )
    pairs2, pos2 = read_host_log_exec_lines(pos)
    assert pairs2 == [(3, 2)]
    assert pos2 == log_path.stat().st_size


def test_read_host_log_exec_lines_handles_truncated_log(tmp_path, monkeypatch):
    import testing.host.cell_structure_live as live_mod

    log_path = tmp_path / "host.log"
    log_path.write_text("[17:38:28] EXEC DETECTED cell=1 order=1\n", encoding="utf-8")
    monkeypatch.setattr(live_mod, "_HOST_LOG_PATH", log_path)
    size = log_path.stat().st_size

    log_path.write_text("", encoding="utf-8")
    pairs, pos = read_host_log_exec_lines(size)
    assert pairs == []
    assert pos == 0


def test_read_kernel_scenario_for_url(tmp_path, monkeypatch):
    import testing.host.cell_structure_live as live_mod

    state_path = tmp_path / "execution_state.json"
    url = "https://www.kaggle.com/code/user/nb/edit"
    state_path.write_text(
        json.dumps({url: {"last_kernel_scenario": "scenario_2_kernel_on"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(live_mod, "_EXECUTION_STATE_PATH", state_path)
    assert read_kernel_scenario_for_url(url) == "scenario_2_kernel_on"


def test_read_live_code_outputs(tmp_path, monkeypatch):
    import testing.host.cell_structure_live as live_mod

    live_root = tmp_path / "live"
    live_root.mkdir()
    url = "https://www.kaggle.com/code/user/nb/edit"
    safe = "".join(c if c.isalnum() else "_" for c in url).strip("_")[:200]
    path = live_root / f"{safe}.json"
    path.write_text(
        json.dumps(
            {
                "tabUrl": url,
                "cells": [
                    {"type": "code", "index": 2, "output": "hello\nworld"},
                    {"type": "markdown", "index": 1, "output": "md"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(live_mod, "_LIVE_ROOT", live_root)
    outputs = read_live_code_outputs(url)
    assert outputs[2] == "hello\nworld"
    assert 1 not in outputs


def test_live_run_session_output_lifecycle(tmp_path, monkeypatch, capsys):
    import testing.host.cell_structure_live as live_mod
    from testing.host.scripts.monitor_cell_runs_live import LiveRunSession

    live_root = tmp_path / "live"
    live_root.mkdir()
    url = "https://www.kaggle.com/code/user/nb/edit"
    safe = "".join(c if c.isalnum() else "_" for c in url).strip("_")[:200]
    path = live_root / f"{safe}.json"
    monkeypatch.setattr(live_mod, "_LIVE_ROOT", live_root)

    def write_output(text: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "tabUrl": url,
                    "kernelScenario": "scenario_2_kernel_on",
                    "cells": [{"type": "code", "index": 1, "output": text}],
                }
            ),
            encoding="utf-8",
        )

    write_output("")
    session = LiveRunSession(url, settle_reads=1)
    base = [
        CellExecutionObservation(index=1, execution_order=1, execution_status="idle"),
    ]
    session.prime(base)
    session._warmup_remaining = 0

    running = [
        CellExecutionObservation(
            index=1,
            execution_order=1,
            execution_status="running",
        )
    ]
    session.tick(log_pairs=[], cells=running)

    out_hash = cells_from_raw_execution(
        [{"type": "code", "index": 1, "output": "done"}]
    )[0].output_hash
    write_output("done")
    changed = [
        CellExecutionObservation(
            index=1,
            execution_order=2,
            execution_title="Cell executed in 0.1s",
            execution_status="executed",
            output_hash=out_hash,
        )
    ]
    session.tick(log_pairs=[], cells=changed)
    session.tick(log_pairs=[], cells=changed)

    captured = capsys.readouterr().out
    assert "] 1\n" in captured or "] 1 |" in captured
    assert "| executed" in captured
    assert "output: done" in captured


def test_live_run_session_running_status_emits_running(capsys):
    from testing.host.scripts.monitor_cell_runs_live import LiveRunSession

    url = "https://www.kaggle.com/code/user/nb/edit"
    session = LiveRunSession(url, settle_reads=1)
    session.prime([CellExecutionObservation(index=3, execution_order=1, execution_status="idle")])
    session._warmup_remaining = 0

    session.tick(
        log_pairs=[],
        cells=[
            CellExecutionObservation(
                index=3,
                execution_order=1,
                execution_status="running",
            )
        ],
    )
    captured = capsys.readouterr().out
    assert "] 3\n" in captured
    assert "| executed" not in captured


def test_live_run_session_startup_baseline_no_runs(capsys):
    from testing.host.scripts.monitor_cell_runs_live import LiveRunSession

    url = "https://www.kaggle.com/code/user/nb/edit"
    session = LiveRunSession(url, settle_reads=2)
    base = [
        CellExecutionObservation(index=i, execution_order=i, execution_status="idle", output_hash=f"h{i}")
        for i in range(1, 6)
    ]
    session.prime(base)
    session._warmup_remaining = 0

    bulk = [
        CellExecutionObservation(
            index=i,
            execution_order=i + 10,
            execution_title="Cell executed in 1.0s",
            execution_status="executed",
            output_hash=f"new{i}",
        )
        for i in range(1, 6)
    ]
    session.tick(log_pairs=[], cells=bulk)
    session.tick(log_pairs=[], cells=bulk)

    captured = capsys.readouterr().out
    assert "| executed" not in captured


def test_live_run_session_bulk_diff_suppressed(capsys):
    from testing.host.scripts.monitor_cell_runs_live import LiveRunSession

    url = "https://www.kaggle.com/code/user/nb/edit"
    session = LiveRunSession(url, settle_reads=1)
    base = [
        CellExecutionObservation(index=1, execution_order=1, execution_status="idle"),
        CellExecutionObservation(index=2, execution_order=2, execution_status="idle"),
        CellExecutionObservation(index=3, execution_order=3, execution_status="idle"),
    ]
    session.prime(base)
    session._warmup_remaining = 0

    changed = [
        CellExecutionObservation(index=1, execution_order=1, execution_status="idle", output_hash="a"),
        CellExecutionObservation(index=2, execution_order=2, execution_status="idle", output_hash="b"),
        CellExecutionObservation(index=3, execution_order=3, execution_status="idle", output_hash="c"),
    ]
    session.tick(log_pairs=[], cells=changed)
    session.tick(log_pairs=[], cells=changed)

    captured = capsys.readouterr().out
    assert "| executed" not in captured


def test_live_run_session_single_cell_run_once(capsys):
    from testing.host.scripts.monitor_cell_runs_live import LiveRunSession

    url = "https://www.kaggle.com/code/user/nb/edit"
    session = LiveRunSession(url, settle_reads=1)
    session.prime([CellExecutionObservation(index=5, execution_order=1, execution_status="idle")])
    session._warmup_remaining = 0

    running = [CellExecutionObservation(index=5, execution_order=1, execution_status="running")]
    session.tick(log_pairs=[], cells=running)
    session.tick(log_pairs=[], cells=running)

    captured = capsys.readouterr().out
    assert captured.count("] 5\n") == 1


def test_live_run_session_output_only_infers_running(capsys):
    """Fast print(1) runs: output appears without a captured RUNNING scrape."""
    from testing.host.scripts.monitor_cell_runs_live import LiveRunSession

    url = "https://www.kaggle.com/code/user/nb/edit"
    session = LiveRunSession(url, settle_reads=1)
    session.prime([CellExecutionObservation(index=2, execution_order=1, execution_status="idle", output_hash="")])
    session._warmup_remaining = 0

    out_hash = cells_from_raw_execution(
        [{"type": "code", "index": 2, "output": "1"}]
    )[0].output_hash
    executed = [
        CellExecutionObservation(
            index=2,
            execution_order=2,
            execution_title="Cell executed in 0.0s",
            execution_status="executed",
            output_hash=out_hash,
        )
    ]
    session.tick(log_pairs=[], cells=executed)
    session.tick(log_pairs=[], cells=executed)

    captured = capsys.readouterr().out
    assert "] 2\n" in captured or "] 2 |" in captured
    assert "] 2 | executed" in captured


def test_live_run_session_streaming_output_grouped(tmp_path, monkeypatch, capsys):
    """Streaming cell: one RUNNING line, one final executed line with full output."""
    import testing.host.cell_structure_live as live_mod
    from testing.host.scripts.monitor_cell_runs_live import LiveRunSession

    live_root = tmp_path / "live"
    live_root.mkdir()
    url = "https://www.kaggle.com/code/user/nb/edit"
    safe = "".join(c if c.isalnum() else "_" for c in url).strip("_")[:200]
    path = live_root / f"{safe}.json"
    monkeypatch.setattr(live_mod, "_LIVE_ROOT", live_root)

    def write_output(text: str, *, status: str = "executed") -> None:
        path.write_text(
            json.dumps(
                {
                    "tabUrl": url,
                    "kernelScenario": "scenario_2_kernel_on",
                    "cells": [
                        {
                            "type": "code",
                            "index": 8,
                            "output": text,
                            "execution_order": 10,
                            "execution_title": "Cell executed in 1.0s",
                            "execution_status": status,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    write_output("")
    session = LiveRunSession(url, settle_reads=2)
    session.prime([CellExecutionObservation(index=8, execution_order=9, execution_status="idle")])
    session._warmup_remaining = 0

    session.tick(
        log_pairs=[],
        cells=[CellExecutionObservation(index=8, execution_order=9, execution_status="running")],
    )

    for partial in ("0", "0\n1", "0\n1\n2"):
        out_hash = cells_from_raw_execution([{"type": "code", "index": 8, "output": partial}])[0].output_hash
        write_output(partial)
        obs = CellExecutionObservation(
            index=8,
            execution_order=10,
            execution_title="Cell executed in 1.0s",
            execution_status="executed",
            output_hash=out_hash,
        )
        session.tick(log_pairs=[], cells=[obs])

    final_hash = cells_from_raw_execution([{"type": "code", "index": 8, "output": "0\n1\n2"}])[0].output_hash
    final = CellExecutionObservation(
        index=8,
        execution_order=10,
        execution_title="Cell executed in 1.0s",
        execution_status="executed",
        output_hash=final_hash,
    )
    import testing.host.scripts.monitor_cell_runs_live as monitor_mod

    session._last_output_change_ts[8] = 999_999.0
    monkeypatch.setattr(monitor_mod.time, "time", lambda: 1_000_000.5)
    session.tick(log_pairs=[], cells=[final])
    session.tick(log_pairs=[], cells=[final])

    captured = capsys.readouterr().out
    assert captured.count("] 8\n") == 1
    assert captured.count("| executed") == 1
    assert "output: 0\\n1\\n2" in captured
    assert "| executed | output: 0\n" not in captured
    assert "| executed | output: 0\\n1\n" not in captured


def test_patch_prompt_execution_signal_running(tmp_path, monkeypatch):
    import testing.host.execution_signal_patch as patch_mod
    import testing.host.persistence_helpers as ph

    live_root = tmp_path / "live"
    meta_root = tmp_path / "meta"
    live_root.mkdir()
    meta_root.mkdir()
    state_path = meta_root / "execution_state.json"
    monkeypatch.setattr(patch_mod, "_LIVE_DIR", live_root)
    monkeypatch.setattr(ph, "EXECUTION_STATE_PATH", state_path)
    monkeypatch.setattr(patch_mod, "read_json_file", ph.read_json_file)
    monkeypatch.setattr(patch_mod, "atomic_write_json", ph._atomic_write_json)
    monkeypatch.setattr(patch_mod, "_load_execution_state", ph._load_execution_state)
    monkeypatch.setattr(patch_mod, "_save_execution_state", ph._save_execution_state)

    url = "https://www.kaggle.com/code/user/nb/edit"
    logs: list[str] = []

    ok = patch_mod.patch_prompt_execution_signal(
        4,
        url,
        "Cell started execution",
        exec_order=9,
        exec_ts=1_700_000_000_000,
        log=logs.append,
    )
    assert ok is True
    assert any("EXEC DETECTED cell=4" in line for line in logs)

    live_path = live_root / ph.get_safe_filename(url)
    live = json.loads(live_path.read_text(encoding="utf-8"))
    cell = next(c for c in live["cells"] if c["index"] == 4)
    assert cell["execution_status"] == "running"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    nb = state[url]
    assert nb["revisions"][nb["active_revision"]]["cells"]["4"]["seen_running"] is True


def test_classify_prompt_phase():
    from testing.host.execution_signal_patch import classify_prompt_phase

    assert classify_prompt_phase("Cell started execution") == "running"
    assert classify_prompt_phase("Cell execution queued") == "queued"
    assert classify_prompt_phase("Cell executed in 0.1s") == "executed"
