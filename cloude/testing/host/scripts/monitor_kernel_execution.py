#!/usr/bin/env python3
"""
Terminal monitor for kernel state + per-cell execution board.

Kernel is OFF or ON only (used internally for reset vs track).
  OFF — no execution info shown
  ON  — track execution; reset on OFF→ON; preserve on page reload while ON

Usage:
  python testing/host/scripts/monitor_kernel_execution.py --url "https://www.kaggle.com/code/codekey/testing-ol/edit"
  python testing/host/scripts/monitor_kernel_execution.py testing-ol --interval 0.8
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

repo_root = Path(__file__).resolve().parents[3]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

_HOST_DIR = Path(__file__).resolve().parents[1]
DATA_ROOT = _HOST_DIR / "data"
EXECUTION_STATE_PATH = DATA_ROOT / "meta" / "execution_state.json"

from testing.host.kernel_execution_policy import (
    SCENARIO_LOADING,
    SCENARIO_OFF,
    SCENARIO_ON,
    TITLE_NOT_EXECUTED,
    TITLE_PENDING,
    TITLE_RUNNING,
    build_execution_board,
    normalize_scenario,
    scenario_is_off,
    scenario_is_on,
)
NOTEBOOKS_ROOT = DATA_ROOT / "notebooks"
LIVE_ROOT = NOTEBOOKS_ROOT / "live"


def _get_safe_filename(url: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in (url or "")).strip("_")
    return f"{safe[:200]}.json"


def _read_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_notebook_url(arg: str) -> str:
    text = str(arg or "").strip()
    if text.startswith("http"):
        return text.rstrip("/")
    needles = [text.lower(), text.lower().replace("-", "_"), text.lower().replace("_", "-")]
    for root in (LIVE_ROOT, NOTEBOOKS_ROOT, NOTEBOOKS_ROOT / "persistent"):
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            data = _read_json(path) or {}
            stem = path.stem.lower()
            tab = str(data.get("tabUrl") or "").lower()
            if any(n in stem or n in tab for n in needles if n):
                url = str(data.get("tabUrl") or "").strip().rstrip("/")
                if url.startswith("http"):
                    return url
    raise SystemExit(f"Could not resolve notebook URL from: {arg!r}")


def _load_execution_state() -> dict:
    data = _read_json(EXECUTION_STATE_PATH)
    return data if isinstance(data, dict) else {}


def _notebook_url_from_arg(arg: str) -> str:
    text = str(arg or "").strip()
    if text.startswith("http"):
        return text.rstrip("/")
    return _resolve_notebook_url(text)


def _live_path(url: str) -> Path:
    return LIVE_ROOT / _get_safe_filename(url)


def _persistent_path(url: str) -> Path:
    return NOTEBOOKS_ROOT / "persistent" / _get_safe_filename(url)


def _load_notebook_json(url: str) -> tuple[dict | None, str]:
    live = _live_path(url)
    if live.is_file():
        return _read_json(live), "live"
    legacy = NOTEBOOKS_ROOT / _get_safe_filename(url)
    if legacy.is_file():
        return _read_json(legacy), "legacy"
    persist = _persistent_path(url)
    if persist.is_file():
        return _read_json(persist), "persistent"
    return None, "none"


def _scenario_for_url(url: str, notebook: dict | None) -> str:
    exec_state = _load_execution_state().get(url) or {}
    if isinstance(notebook, dict) and notebook.get("kernelScenario"):
        return normalize_scenario(str(notebook.get("kernelScenario")))
    if exec_state.get("last_kernel_scenario"):
        return normalize_scenario(str(exec_state.get("last_kernel_scenario")))
    return "unknown"


def _effective_scenario(scenario: str, prev: str | None) -> str:
    """Hold last stable OFF/ON through editor_loading."""
    norm = normalize_scenario(scenario)
    if norm == SCENARIO_LOADING and prev:
        return normalize_scenario(prev)
    return norm


def _execution_fingerprint(board: list[dict]) -> str:
    parts = [
        {
            "cell_index": row.get("cell_index"),
            "execution_order": row.get("execution_order"),
            "execution_title": row.get("execution_title"),
            "ran_this_session": row.get("ran_this_session"),
        }
        for row in board
    ]
    return json.dumps(parts, sort_keys=True, ensure_ascii=False)


def _kernel_state_line(scenario: str) -> str:
    if scenario_is_off(scenario):
        return f"OFF ({SCENARIO_OFF}) - no execution tracking"
    if scenario_is_on(scenario):
        return f"ON ({SCENARIO_ON}) - execution tracked, preserved across reload"
    return f"unknown ({scenario})"


def _print_header(url: str, scenario: str, source: str, reason: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 72)
    print(f"[{now}] {reason}")
    print(f"URL: {url}")
    print(f"Kernel state: {_kernel_state_line(scenario)}")
    print(f"Snapshot: {source}")
    print("=" * 72)


def _row_is_session_executed(row: dict) -> bool:
    title = str(row.get("execution_title") or "")
    order = row.get("execution_order")
    if order is None:
        return False
    if TITLE_NOT_EXECUTED in title or TITLE_PENDING in title or TITLE_RUNNING in title:
        return False
    return "Cell executed" in title or title.startswith("Execution")


def _remember_session_execution(board: list[dict], session_exec: dict[int, dict]) -> None:
    for row in board:
        idx = row.get("cell_index")
        if idx is None or not _row_is_session_executed(row):
            continue
        session_exec[int(idx)] = {
            "execution_order": row.get("execution_order"),
            "execution_title": row.get("execution_title"),
            "ran_this_session": bool(row.get("ran_this_session")),
            "has_output": bool(row.get("has_output")),
        }


def _apply_session_execution(board: list[dict], session_exec: dict[int, dict]) -> list[dict]:
    if not session_exec:
        return board
    merged: list[dict] = []
    for row in board:
        r = dict(row)
        idx = r.get("cell_index")
        if idx is not None and not _row_is_session_executed(r):
            cached = session_exec.get(int(idx))
            if cached:
                r["execution_order"] = cached.get("execution_order")
                r["execution_title"] = cached.get("execution_title")
                r["ran_this_session"] = cached.get("ran_this_session", True)
                if cached.get("has_output"):
                    r["has_output"] = True
        merged.append(r)
    return merged


def _build_display_board(
    cells: list[dict],
    scenario: str,
    session_exec: dict[int, dict],
) -> list[dict]:
    if scenario_is_off(scenario):
        return build_execution_board(cells, kernel_scenario=SCENARIO_OFF)

    board = build_execution_board(cells, kernel_scenario=SCENARIO_ON)
    _remember_session_execution(board, session_exec)
    return _apply_session_execution(board, session_exec)


def _off_to_on(prev: str, scenario: str) -> bool:
    return scenario_is_on(scenario) and scenario_is_off(prev)


def _on_to_off(prev: str, scenario: str) -> bool:
    return scenario_is_off(scenario) and scenario_is_on(prev)


def _scenario_change_reason(prev: str, scenario: str) -> str:
    if _off_to_on(prev, scenario):
        return "KERNEL ON - execution reset for new session"
    if _on_to_off(prev, scenario):
        return "KERNEL OFF - execution info cleared"
    if scenario_is_off(scenario):
        return "KERNEL OFF - cell list"
    if scenario_is_on(scenario):
        return "KERNEL ON - cell list"
    return f"KERNEL STATE CHANGED ({prev} -> {scenario})"


def _print_board(board: list[dict], scenario: str, *, changed_cells: list[int] | None = None) -> None:
    if not board:
        print("(no code cells)")
        return
    changed_set = set(changed_cells or [])
    print(f"\n{'Cell':>5}  {'Order':>6}  Details")
    print("-" * 72)
    for row in board:
        idx = row.get("cell_index", "?")
        marker = " *" if idx in changed_set else "  "

        if scenario_is_off(scenario):
            detail = "has_output" if row.get("has_output") else "-"
            print(f"{marker}{idx:>5}  {'-':>6}  {detail}")
            continue

        order = row.get("execution_order")
        order_s = str(order) if order is not None else "-"
        title = row.get("execution_title") or ""
        parts = []
        if title:
            parts.append(str(title))
        if row.get("ran_this_session"):
            parts.append("ran_this_session")
        if row.get("has_output"):
            parts.append("has_output")
        detail = " | ".join(parts) if parts else "-"
        print(f"{marker}{idx:>5}  {order_s:>6}  {detail}")
    if changed_set:
        print(f"\n  * = execution changed ({sorted(changed_set)})")
    print()


def _changed_cell_indices(prev: dict[int, dict], board: list[dict]) -> list[int]:
    out: list[int] = []
    for row in board:
        ci = row.get("cell_index")
        if ci is None:
            continue
        if prev.get(int(ci)) != row:
            out.append(int(ci))
    return out


def monitor(url: str, *, interval: float = 1.0) -> None:
    print(f"Watching {url}")
    print("Prints: kernel off (once) | kernel on reset (once) | cell runs while on")
    print("Ctrl+C to stop\n")

    prev_scenario: str | None = None
    prev_exec_fp: str | None = None
    prev_cells: dict[int, dict] = {}
    session_exec: dict[int, dict] = {}
    missing_warned = False

    while True:
        notebook, source = _load_notebook_json(url)
        if not isinstance(notebook, dict):
            if not missing_warned:
                print(f"[WARN] No notebook JSON yet for {url} - waiting for scrape...")
                missing_warned = True
            time.sleep(interval)
            continue
        missing_warned = False

        raw_scenario = _scenario_for_url(url, notebook)
        scenario = _effective_scenario(raw_scenario, prev_scenario)

        if prev_scenario is not None and scenario != prev_scenario:
            if _off_to_on(prev_scenario, scenario) or _on_to_off(prev_scenario, scenario):
                session_exec.clear()

        board = _build_display_board(notebook.get("cells") or [], scenario, session_exec)
        exec_fp = _execution_fingerprint(board)

        if prev_scenario is None:
            _print_header(url, scenario, source, _scenario_change_reason("", scenario))
            _print_board(board, scenario)
            prev_scenario = scenario
            prev_exec_fp = exec_fp
            prev_cells = {int(r["cell_index"]): r for r in board if r.get("cell_index") is not None}
            time.sleep(interval)
            continue

        if normalize_scenario(raw_scenario) == SCENARIO_LOADING:
            time.sleep(interval)
            continue

        reason: str | None = None
        changed_cells: list[int] = []

        if scenario != prev_scenario:
            reason = _scenario_change_reason(prev_scenario, scenario)
        elif scenario_is_on(scenario) and exec_fp != prev_exec_fp:
            reason = "CELL EXECUTION UPDATED"
            changed_cells = _changed_cell_indices(prev_cells, board)

        if reason is None:
            time.sleep(interval)
            continue

        if changed_cells and reason == "CELL EXECUTION UPDATED":
            reason = f"{reason} - cells {changed_cells}"

        _print_header(url, scenario, source, reason)
        _print_board(board, scenario, changed_cells=changed_cells)

        prev_scenario = scenario
        prev_exec_fp = exec_fp
        prev_cells = {int(r["cell_index"]): r for r in board if r.get("cell_index") is not None}
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor kernel state and cell execution.")
    parser.add_argument("notebook", nargs="?", default="", help="URL or short name (e.g. testing-ol)")
    parser.add_argument("--url", default="", help="Notebook URL")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    import os

    if os.environ.get("KERNEL_EXECUTION_METADATA_ENABLED", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        raise SystemExit(
            "Kernel execution metadata is disabled. "
            "Set KERNEL_EXECUTION_METADATA_ENABLED=1 to enable kernel execution monitoring."
        )

    url = (args.url or args.notebook or "").strip()
    if not url:
        parser.error("Provide notebook URL or path")
    if not url.startswith("http"):
        url = _notebook_url_from_arg(url)

    try:
        if args.once:
            notebook, source = _load_notebook_json(url)
            if not isinstance(notebook, dict):
                raise SystemExit(f"No notebook JSON for {url}")
            scenario = _scenario_for_url(url, notebook)
            scenario = normalize_scenario(scenario)
            if scenario == SCENARIO_LOADING:
                scenario = SCENARIO_OFF
            board = _build_display_board(notebook.get("cells") or [], scenario, {})
            _print_header(url, scenario, source, "SNAPSHOT (--once)")
            _print_board(board, scenario)
            return
        monitor(url, interval=max(0.3, float(args.interval)))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
