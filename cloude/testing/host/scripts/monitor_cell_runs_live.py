#!/usr/bin/env python3
"""
Live terminal monitor for notebook cell execution (run / running / done).

Uses cell_execution_observer + live scrape JSON diff (same approach as
monitor_cell_additions.py for structure). Polls data/notebooks/live/ for:
  - execution_status (running / queued / executed from scraped DOM)
  - execution_order and execution_title changes
  - output hash changes
Optional fast path: host.log EXEC DETECTED lines and execution_state.json revisions.

Prerequisites:
  - host.py running (extension pushes NOTEBOOK_DATA)
  - Notebook tab open in Chrome with kernel ON

Usage:
  python testing/host/scripts/monitor_cell_runs_live.py testing-ol
  python testing/host/scripts/monitor_cell_runs_live.py --url "https://www.kaggle.com/code/.../edit"
  python testing/host/scripts/monitor_cell_runs_live.py testing-ol --interval 0.05
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from testing.host.cell_execution_observer import (  # noqa: E402
    CellExecutionEvent,
    CellExecutionObservation,
    CellExecutionTracker,
    ExecutionEventKind,
    execution_index_map,
)
from testing.host.cell_structure_live import (  # noqa: E402
    _HOST_LOG_PATH,
    _LIVE_ROOT,
    _read_json,
    execution_watch_mtimes,
    merge_execution_observations,
    read_host_log_exec_lines,
    read_kernel_scenario_for_url,
    read_live_code_outputs,
    read_live_execution_cells,
    read_revision_execution_cells,
)
from testing.host.kernel_execution_policy import (  # noqa: E402
    scenario_is_off,
)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _resolve_url(arg: str) -> str:
    text = str(arg or "").strip().rstrip("/")
    if text.startswith("http"):
        return text
    needles = [text.lower(), text.lower().replace("-", "_"), text.lower().replace("_", "-")]
    if _LIVE_ROOT.is_dir():
        for path in _LIVE_ROOT.glob("*.json"):
            data = _read_json(path) or {}
            tab = str(data.get("tabUrl") or "").lower()
            if any(n in tab for n in needles if n):
                return str(data.get("tabUrl") or "").strip().rstrip("/")
    raise SystemExit(
        f"Could not resolve notebook URL from {arg!r}. "
        "Open the notebook in Chrome (host running) or pass --url."
    )


def _format_output(text: str, *, limit: int = 240) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    preview = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    if len(preview) > limit:
        return preview[:limit] + "..."
    return preview


def _kernel_allows_tracking(scenario: str) -> bool:
    if scenario_is_off(scenario):
        return False
    return True


def _load_observations(url: str) -> tuple[list[CellExecutionObservation], dict]:
    live, meta = read_live_execution_cells(url)
    revision = read_revision_execution_cells(url)
    merged = merge_execution_observations(live, revision)
    meta = dict(meta)
    scenario = str(meta.get("kernelScenario") or "").strip()
    if not scenario:
        scenario = read_kernel_scenario_for_url(url)
    meta["kernelScenario"] = scenario
    meta["merged_count"] = len(merged)
    meta["revision_count"] = len(revision)
    return merged, meta


@dataclass
class LiveRunSession:
    """Per-cell idle → RUNNING → DONE lifecycle from live scrape diffs."""

    url: str
    settle_reads: int = 2
    tracker: CellExecutionTracker = field(init=False)
    running_printed: set[int] = field(default_factory=set)
    active: set[int] = field(default_factory=set)
    _run_in_progress: set[int] = field(default_factory=set)
    reported_log_pairs: set[tuple[int, int]] = field(default_factory=set)
    _done_stable_hash: dict[int, str] = field(default_factory=dict)
    _done_stable_reads: dict[int, int] = field(default_factory=dict)
    _output_versions: dict[int, int] = field(default_factory=dict)
    _last_output_change_ts: dict[int, float] = field(default_factory=dict)
    _warmup_remaining: int = 0

    def __post_init__(self) -> None:
        self.tracker = CellExecutionTracker(settle_reads=max(1, self.settle_reads))

    def prime(self, cells: list[CellExecutionObservation]) -> None:
        self.tracker.reset(cells)
        self.running_printed = {idx for idx, c in execution_index_map(cells).items() if c.is_running}
        self.active = set(self.running_printed)
        self._run_in_progress = set(self.running_printed)
        self._warmup_remaining = max(1, self.settle_reads)
        for cell in cells:
            if cell.execution_order is not None and cell.title_executed:
                self.reported_log_pairs.add((cell.index, cell.execution_order))

    def _begin_run(self, index: int, order: int | None = None) -> None:
        """Mark a cell as actively running; print the cell index once per run."""
        if index in self._run_in_progress:
            self.active.add(index)
            return
        self._run_in_progress.add(index)
        self.running_printed.add(index)
        self.active.add(index)
        self._done_stable_hash.pop(index, None)
        self._done_stable_reads.pop(index, None)
        self._output_versions.pop(index, None)
        self._last_output_change_ts.pop(index, None)
        print(f"[{_ts()}] {index}", flush=True)

    def _emit_done(self, index: int, order: int | None = None, *, infer_running: bool = False) -> bool:
        """Emit final executed line once output and status have settled. Returns True if printed."""
        in_run = index in self._run_in_progress
        if not in_run and index not in self.active and index not in self.running_printed:
            return False

        outputs = read_live_code_outputs(self.url)
        output_text = _format_output(outputs.get(index, ""))

        # During an active run, suppress empty executed lines until output appears or run ends.
        if in_run and not output_text:
            need = max(1, int(self.settle_reads))
            stable = self._done_stable_reads.get(index, 0)
            if stable < need * 2:
                return False

        if infer_running and index not in self.running_printed:
            print(f"[{_ts()}] {index}", flush=True)
            self.running_printed.add(index)

        if output_text:
            print(f"[{_ts()}] {index} | executed | output: {output_text}", flush=True)
        else:
            print(f"[{_ts()}] {index} | executed", flush=True)

        self.active.discard(index)
        self.running_printed.discard(index)
        self._run_in_progress.discard(index)
        self._done_stable_hash.pop(index, None)
        self._done_stable_reads.pop(index, None)
        self._output_versions.pop(index, None)
        self._last_output_change_ts.pop(index, None)
        return True

    def _output_quiesce_seconds(self, index: int) -> float:
        """Longer silence after multiple output revisions (streaming cells)."""
        if self._output_versions.get(index, 0) > 1:
            return 0.5
        return 0.0

    def _on_exec_detected(self, cell_idx: int, order: int) -> None:
        pair = (cell_idx, order)
        if pair in self.reported_log_pairs:
            return
        self.reported_log_pairs.add(pair)
        self._begin_run(cell_idx, order)

    def _handle_tracker_event(self, ev: CellExecutionEvent) -> None:
        if ev.kind == ExecutionEventKind.DONE:
            # Defer done line until output hash settles (_maybe_complete_active).
            self.active.add(ev.index)
            return
        if ev.kind == ExecutionEventKind.RUNNING:
            self._begin_run(ev.index, ev.execution_order)
        elif ev.kind == ExecutionEventKind.RUN:
            # Output hash changes mid-run must not re-emit RUNNING or executed.
            if ev.index in self._run_in_progress:
                self.active.add(ev.index)
            elif ev.index not in self.running_printed:
                self._begin_run(ev.index, ev.execution_order)
            else:
                self.active.add(ev.index)

    def _sync_warmup(self, cells: list[CellExecutionObservation]) -> bool:
        """Absorb notebook resync without emitting diff-based run events."""
        if self._warmup_remaining <= 0:
            return False
        self.tracker.reset(cells)
        self._warmup_remaining -= 1
        return True

    def _maybe_complete_active(self, cells: list[CellExecutionObservation]) -> None:
        current = execution_index_map(cells)
        need = max(1, int(self.settle_reads))
        for idx in list(self.active):
            cell = current.get(idx)
            if cell is None:
                continue
            if cell.is_running:
                self._done_stable_hash.pop(idx, None)
                self._done_stable_reads.pop(idx, None)
                continue
            if idx not in self._run_in_progress and idx not in self.running_printed:
                self.active.discard(idx)
                continue
            h = cell.output_hash
            prev_h = self._done_stable_hash.get(idx)
            if prev_h == h:
                count = self._done_stable_reads.get(idx, 0) + 1
                self._done_stable_reads[idx] = count
            else:
                if prev_h is not None:
                    self._output_versions[idx] = self._output_versions.get(idx, 0) + 1
                self._done_stable_hash[idx] = h
                self._done_stable_reads[idx] = 1
                self._last_output_change_ts[idx] = time.time()
            if self._done_stable_reads.get(idx, 0) < need:
                continue
            last_change = self._last_output_change_ts.get(idx, 0.0)
            if time.time() - last_change < self._output_quiesce_seconds(idx):
                continue
            infer = idx not in self.running_printed
            self._emit_done(idx, cell.execution_order, infer_running=infer)

    def tick(
        self,
        *,
        log_pairs: list[tuple[int, int]],
        cells: list[CellExecutionObservation],
    ) -> None:
        for cell_idx, order in log_pairs:
            self._on_exec_detected(cell_idx, order)

        if cells and self._sync_warmup(cells):
            return

        if not cells:
            return

        result = self.tracker.observe(cells)
        run_events = [e for e in result.events if e.kind == ExecutionEventKind.RUN]
        if len(run_events) > 1:
            # Multi-cell change in one settle cycle — treat as resync, not runs.
            self.tracker.reset(cells)
            return

        for ev in result.events:
            self._handle_tracker_event(ev)

        self._maybe_complete_active(cells)


def monitor(
    url: str,
    *,
    interval: float = 0.001,
    settle_reads: int = 2,
    verbose: bool = False,
) -> None:
    session = LiveRunSession(url, settle_reads=max(1, settle_reads))
    primed = False
    prev_mtimes = execution_watch_mtimes(url)
    last_activity = 0.0
    host_log_pos = 0
    if _HOST_LOG_PATH.is_file():
        try:
            host_log_pos = _HOST_LOG_PATH.stat().st_size
        except OSError:
            host_log_pos = 0

    print(f"Cell execution monitor (live) — {url}")
    print(
        f"Poll {interval}s | signals: live JSON scrape, execution_state.json, "
        f"host.log | settle_reads={session.settle_reads}"
    )
    print("─" * 72, flush=True)

    while True:
        mtimes = execution_watch_mtimes(url)
        files_changed = mtimes != prev_mtimes
        prev_mtimes = mtimes
        if files_changed:
            last_activity = time.time()

        log_pairs, host_log_pos = read_host_log_exec_lines(host_log_pos)

        burst = files_changed or (time.time() - last_activity < 2.0)
        reads = 3 if burst else 1
        cells: list[CellExecutionObservation] = []
        meta: dict = {}
        for _ in range(reads):
            cells, meta = _load_observations(url)
            if primed:
                session.tick(log_pairs=log_pairs, cells=cells)
                log_pairs = []
            if burst and reads > 1:
                time.sleep(interval)

        if not primed:
            if cells:
                session.prime(cells)
                primed = True
                n = len(cells)
                print(f"[{_ts()}] baseline {n} code cell(s) tracked", flush=True)
                if verbose and meta.get("path"):
                    print(f"[{_ts()}] live file: {meta['path']}", flush=True)
                scenario = str(meta.get("kernelScenario") or "")
                if scenario and not _kernel_allows_tracking(scenario):
                    print(
                        f"[{_ts()}] kernel not ON ({scenario}) — waiting for kernel",
                        flush=True,
                    )
            elif verbose:
                print(f"[{_ts()}] waiting for live scrape…", flush=True)
            time.sleep(interval)
            continue

        scenario = str(meta.get("kernelScenario") or "")
        if scenario and scenario_is_off(scenario):
            if verbose:
                print(f"[{_ts()}] kernel OFF — pausing run events", flush=True)
            time.sleep(interval)
            continue

        if not _kernel_allows_tracking(scenario) and not session.active:
            time.sleep(interval)
            continue

        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect notebook cell runs from live scrape JSON diffs.",
    )
    parser.add_argument("notebook", nargs="?", default="", help="URL or slug (e.g. testing-ol)")
    parser.add_argument("--url", default="", help="Notebook /edit URL")
    parser.add_argument("--interval", type=float, default=0.001, help="Poll seconds (default 0.001)")
    parser.add_argument(
        "--settle-reads",
        type=int,
        default=2,
        help="Identical reads before accepting settled run signals (default 2)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    url = (args.url or args.notebook or "").strip()
    if not url:
        parser.error("Provide notebook URL or short name")
    if not url.startswith("http"):
        url = _resolve_url(url)

    try:
        monitor(
            url,
            interval=max(0.001, float(args.interval)),
            settle_reads=max(1, int(args.settle_reads)),
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
