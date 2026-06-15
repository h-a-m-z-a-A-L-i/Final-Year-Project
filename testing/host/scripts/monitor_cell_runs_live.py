#!/usr/bin/env python3
"""
Live terminal monitor for notebook cell execution (run / running / done).

Uses cell_execution_observer + fastest local signals (not persistent merge):
  1. host.log EXEC DETECTED lines (fastest when KERNEL_EXECUTION_METADATA_ENABLED=1)
  2. execution_state.json revision cells (host writes on each NOTEBOOK_DATA)
  3. live scrape JSON (extension sendTabs ~5s; PROMPT_SIGNAL can trigger sooner)

50ms poll watches file mtimes; burst-reads on change. Live JSON alone is too slow
for sub-second runs — host.log + execution_state bridge the gap.

Prerequisites:
  - host.py running (extension pushes NOTEBOOK_DATA)
  - Notebook tab open in Chrome with kernel ON

Usage:
  python testing/host/scripts/monitor_cell_runs_live.py testing-ol
  python testing/host/scripts/monitor_cell_runs_live.py --url "https://www.kaggle.com/code/.../edit"
  python testing/host/scripts/monitor_cell_runs_live.py testing-ol --interval 0.05

Without KERNEL_EXECUTION_METADATA_ENABLED=1, detection falls back to output-hash
changes in live scrape (slower; may miss very brief empty-output runs).
"""

from __future__ import annotations

import argparse
import sys
import time
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
)
from testing.host.cell_structure_live import (  # noqa: E402
    _HOST_LOG_PATH,
    _LIVE_ROOT,
    _read_json,
    execution_watch_mtimes,
    merge_execution_observations,
    read_host_log_exec_lines,
    read_live_execution_cells,
    read_revision_execution_cells,
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


def _scenario_is_on(scenario: str) -> bool:
    s = str(scenario or "").strip().lower()
    return "kernel_on" in s or s == "scenario_2_kernel_on"


def _print_event(ev: CellExecutionEvent) -> None:
    order = ev.execution_order
    order_part = f" order={order}" if order is not None else ""
    if ev.kind == ExecutionEventKind.RUNNING:
        print(f"[{_ts()}] RUNNING cell={ev.index}{order_part}", flush=True)
    elif ev.kind == ExecutionEventKind.RUN:
        print(f"[{_ts()}] RUN cell={ev.index}{order_part}", flush=True)
    elif ev.kind == ExecutionEventKind.DONE:
        print(f"[{_ts()}] DONE cell={ev.index}", flush=True)


def _synthetic_run_from_log(cell_idx: int, order: int) -> CellExecutionEvent:
    return CellExecutionEvent(
        kind=ExecutionEventKind.RUN,
        index=cell_idx,
        execution_order=order,
        execution_title=f"Cell executed (Execution #{order})",
        execution_status="executed",
        reason=f"host_log order={order}",
    )


def _load_observations(url: str) -> tuple[list[CellExecutionObservation], dict]:
    live, meta = read_live_execution_cells(url)
    revision = read_revision_execution_cells(url)
    merged = merge_execution_observations(live, revision)
    meta = dict(meta)
    meta["merged_count"] = len(merged)
    meta["revision_count"] = len(revision)
    return merged, meta


def monitor(
    url: str,
    *,
    interval: float = 0.05,
    settle_reads: int = 2,
    verbose: bool = False,
) -> None:
    tracker = CellExecutionTracker(settle_reads=max(1, settle_reads))
    primed = False
    prev_mtimes = execution_watch_mtimes(url)
    last_activity = 0.0
    host_log_pos = 0
    if _HOST_LOG_PATH.is_file():
        try:
            host_log_pos = _HOST_LOG_PATH.stat().st_size
        except OSError:
            host_log_pos = 0
    reported_log_pairs: set[tuple[int, int]] = set()

    print(f"Cell execution monitor (live) — {url}")
    print(
        f"Poll {interval}s | signals: host.log, execution_state.json, live JSON "
        f"(sendTabs ~5s) | settle_reads={tracker.settle_reads}"
    )
    print("─" * 72, flush=True)

    while True:
        mtimes = execution_watch_mtimes(url)
        files_changed = mtimes != prev_mtimes
        prev_mtimes = mtimes
        if files_changed:
            last_activity = time.time()

        log_pairs, host_log_pos = read_host_log_exec_lines(host_log_pos)
        for cell_idx, order in log_pairs:
            pair = (cell_idx, order)
            if pair in reported_log_pairs:
                continue
            reported_log_pairs.add(pair)
            _print_event(_synthetic_run_from_log(cell_idx, order))

        cells, meta = _load_observations(url)
        scenario = str(meta.get("kernelScenario") or "")

        if not cells:
            if verbose:
                print(f"[{_ts()}] waiting for live scrape…", flush=True)
            time.sleep(interval)
            continue

        if not _scenario_is_on(scenario) and primed:
            if verbose:
                print(f"[{_ts()}] kernel not ON ({scenario or 'unknown'}) — pausing events", flush=True)
            time.sleep(interval)
            continue

        if not primed:
            tracker.reset(cells)
            primed = True
            for cell in cells:
                order = cell.execution_order
                if order is not None and cell.title_executed:
                    reported_log_pairs.add((cell.index, order))
            n = len(cells)
            print(f"[{_ts()}] baseline {n} code cell(s) tracked", flush=True)
            if verbose and meta.get("path"):
                print(f"[{_ts()}] live file: {meta['path']}", flush=True)
            time.sleep(interval)
            continue

        burst = files_changed or (time.time() - last_activity < 2.0)
        if burst:
            for _ in range(3):
                cells, meta = _load_observations(url)
                events = tracker.observe(cells)
                for ev in events.events:
                    _print_event(ev)
                if events.events:
                    break
                time.sleep(min(interval, 0.02))
        else:
            events = tracker.observe(cells)
            for ev in events.events:
                _print_event(ev)

        sleep_for = interval
        if burst or files_changed:
            sleep_for = min(interval, 0.02)
        time.sleep(sleep_for)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect notebook cell runs from live scrape + fast local signals.",
    )
    parser.add_argument("notebook", nargs="?", default="", help="URL or slug (e.g. testing-ol)")
    parser.add_argument("--url", default="", help="Notebook /edit URL")
    parser.add_argument("--interval", type=float, default=0.05, help="Poll seconds (default 0.05)")
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
            interval=max(0.02, float(args.interval)),
            settle_reads=max(1, int(args.settle_reads)),
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
