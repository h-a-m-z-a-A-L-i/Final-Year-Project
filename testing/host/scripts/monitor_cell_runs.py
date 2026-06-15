#!/usr/bin/env python3
"""
Detect notebook cell runs and print execution info to the console.

Merges live + legacy/persistent notebook JSON; watches file mtimes for fast pickup.
Only reports while kernel is ON. Resets tracking on OFF->ON.

Usage:
  python testing/host/scripts/monitor_cell_runs.py testing-ol
  python testing/host/scripts/monitor_cell_runs.py --url "https://www.kaggle.com/code/codekey/testing-ol/edit"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# Self-contained paths — do not import testing.host.config (loads Cerebras client).
_HOST_DIR = Path(__file__).resolve().parents[1]
DATA_ROOT = _HOST_DIR / "data"
NOTEBOOKS_ROOT = DATA_ROOT / "notebooks"
LIVE_ROOT = NOTEBOOKS_ROOT / "live"
EXECUTION_STATE_PATH = DATA_ROOT / "meta" / "execution_state.json"
HOST_LOG_PATH = DATA_ROOT / "logs" / "host.log"

SCENARIO_OFF = "scenario_1_new_notebook_off"
SCENARIO_ON = "scenario_2_kernel_on"
SCENARIO_LOADING = "editor_loading"


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


def _normalize_scenario(raw: str) -> str:
    s = str(raw or "unknown").strip().lower()
    if s in (SCENARIO_OFF, SCENARIO_ON, SCENARIO_LOADING):
        return s
    if "off" in s and "notebook" in s:
        return SCENARIO_OFF
    if "kernel_on" in s or "fresh" in s or "reload" in s:
        return SCENARIO_ON
    if "loading" in s:
        return SCENARIO_LOADING
    return "unknown"


def _scenario_is_off(scenario: str) -> bool:
    return _normalize_scenario(scenario) == SCENARIO_OFF


def _scenario_is_on(scenario: str) -> bool:
    return _normalize_scenario(scenario) == SCENARIO_ON


def _resolve_notebook_url(arg: str) -> str:
    text = str(arg or "").strip()
    if text.startswith("http"):
        return text.rstrip("/")
    needles = [text.lower(), text.lower().replace("-", "_"), text.lower().replace("_", "-")]
    for root in (LIVE_ROOT, NOTEBOOKS_ROOT, NOTEBOOKS_ROOT / "persistent"):
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            stem = path.stem.lower()
            data = _read_json(path) or {}
            tab = str(data.get("tabUrl") or "").lower()
            if any(n in stem or n in tab for n in needles if n):
                url = str(data.get("tabUrl") or "").strip().rstrip("/")
                if url.startswith("http"):
                    return url
    raise SystemExit(f"Could not resolve notebook URL from: {arg!r}")


def _paths_for_url(url: str) -> list[Path]:
    fname = _get_safe_filename(url)
    return [
        LIVE_ROOT / fname,
        NOTEBOOKS_ROOT / fname,
        NOTEBOOKS_ROOT / "persistent" / fname,
        EXECUTION_STATE_PATH,
    ]


def _file_mtouches(paths: list[Path]) -> tuple[float, ...]:
    out: list[float] = []
    for path in paths:
        try:
            out.append(path.stat().st_mtime)
        except OSError:
            out.append(0.0)
    return tuple(out)


def _revision_cells(url: str) -> dict[str, dict]:
    state = _read_json(EXECUTION_STATE_PATH) or {}
    nb = state.get(url) if isinstance(state, dict) else None
    if not isinstance(nb, dict):
        return {}
    rev_hash = nb.get("active_revision")
    revisions = nb.get("revisions") if isinstance(nb.get("revisions"), dict) else {}
    rev = revisions.get(rev_hash) if rev_hash else None
    if not isinstance(rev, dict):
        return {}
    cells = rev.get("cells")
    return dict(cells) if isinstance(cells, dict) else {}


def _revision_sig(row: dict) -> dict:
    title = str(row.get("title") or "").strip()
    order = row.get("baseline_order")
    try:
        order = int(order) if order is not None else None
    except (TypeError, ValueError):
        order = None
    return {
        "execution_order": order,
        "execution_title": title,
        "seen_running": bool(row.get("seen_running")),
    }


def _parse_host_log_exec(lines: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for line in lines.splitlines():
        m = re.search(r"EXEC DETECTED cell=(\d+) order=(\d+)", line)
        if m:
            found.append((int(m.group(1)), int(m.group(2))))
    return found


def _notebook_paths(url: str) -> list[tuple[str, Path]]:
    fname = _get_safe_filename(url)
    return [
        ("live", LIVE_ROOT / fname),
        ("legacy", NOTEBOOKS_ROOT / fname),
        ("persistent", NOTEBOOKS_ROOT / "persistent" / fname),
    ]


def _title_is_executed(title: str) -> bool:
    t = str(title or "").strip().lower()
    if not t or "not executed yet" in t:
        return False
    return "cell executed" in t or t.startswith("execution")


def _merge_cells(old: dict | None, new: dict) -> dict:
    if not old:
        return dict(new)
    out = dict(old)
    out["input"] = new.get("input") if new.get("input") is not None else out.get("input")
    if str(new.get("output") or "") != str(out.get("output") or ""):
        out["output"] = new.get("output")

    new_title = str(new.get("execution_title") or "").strip()
    old_title = str(out.get("execution_title") or "").strip()
    if _title_is_executed(new_title) or (not _title_is_executed(old_title) and new_title):
        out["execution_title"] = new_title

    try:
        new_order = int(new["execution_order"]) if new.get("execution_order") is not None else None
    except (TypeError, ValueError):
        new_order = None
    try:
        old_order = int(out["execution_order"]) if out.get("execution_order") is not None else None
    except (TypeError, ValueError):
        old_order = None
    if new_order is not None and (old_order is None or new_order >= old_order):
        out["execution_order"] = new_order

    status_rank = {"running": 3, "queued": 2, "executed": 1, "idle": 0}
    new_status = str(new.get("execution_status") or "idle").strip().lower()
    old_status = str(out.get("execution_status") or "idle").strip().lower()
    if status_rank.get(new_status, 0) >= status_rank.get(old_status, 0):
        out["execution_status"] = new_status
    return out


def _load_sources(url: str) -> tuple[dict | None, dict | None, str]:
    """Return (live_notebook, merged_notebook, source_label)."""
    live_data: dict | None = None
    snapshots: list[tuple[str, dict, str]] = []

    for label, path in _notebook_paths(url):
        if not path.is_file():
            continue
        data = _read_json(path)
        if not data:
            continue
        if label == "live":
            live_data = data
        snapshots.append((label, data, str(data.get("lastUpdated") or "")))

    if not snapshots:
        return None, None, "none"

    snapshots.sort(key=lambda item: item[2])
    merged_cells: dict[int, dict] = {}
    kernel_scenario = "unknown"
    tab_url = url
    sources: list[str] = []

    for label, data, _ts in snapshots:
        sources.append(label)
        kernel_scenario = str(data.get("kernelScenario") or kernel_scenario)
        tab_url = str(data.get("tabUrl") or tab_url)
        for cell in data.get("cells") or []:
            if not isinstance(cell, dict) or str(cell.get("type") or "code") != "code":
                continue
            try:
                idx = int(cell["index"])
            except (TypeError, ValueError, KeyError):
                continue
            merged_cells[idx] = _merge_cells(merged_cells.get(idx), cell)

    merged = {
        "tabUrl": tab_url,
        "kernelScenario": kernel_scenario,
        "cells": list(merged_cells.values()),
        "lastUpdated": snapshots[-1][2],
    }
    return live_data, merged, "+".join(dict.fromkeys(sources))


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()[:12]


def _cell_sig(cell: dict) -> dict:
    title = str(cell.get("execution_title") or "").strip()
    order = cell.get("execution_order")
    try:
        order = int(order) if order is not None else None
    except (TypeError, ValueError):
        order = None
    return {
        "execution_order": order,
        "execution_title": title,
        "execution_status": str(cell.get("execution_status") or "idle").strip().lower(),
        "output_hash": _hash_text(str(cell.get("output") or "")),
        "output_preview": str(cell.get("output") or "").strip().replace("\n", " ")[:160],
        "input_preview": str(cell.get("input") or "").strip().replace("\n", " ")[:100],
    }


def _code_cells(notebook: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for cell in notebook.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        if str(cell.get("type") or "code") != "code":
            continue
        try:
            out[int(cell["index"])] = cell
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _best_order(live: dict, merged: dict, rev: dict) -> int | None:
    for source in (merged, live, rev):
        order = source.get("execution_order")
        if order is not None:
            try:
                return int(order)
            except (TypeError, ValueError):
                continue
    return None


def _best_title(live: dict, merged: dict, rev: dict) -> str:
    for source in (merged, live, rev):
        title = str(source.get("execution_title") or "").strip()
        if title:
            return title
    return ""


def _init_session_state(
    live_cells: dict[int, dict],
    merged_cells: dict[int, dict],
    revision_rows: dict[str, dict],
    live_prev: dict[int, dict],
    merged_prev: dict[int, dict],
    rev_prev: dict[int, dict],
    reported_pairs: set[tuple[int, int]],
) -> int:
    """Seed reported (cell, order) pairs already visible; return session global max order."""
    session_global_max = 0
    for idx in set(live_cells) | set(merged_cells):
        live_cell = live_cells.get(idx) or merged_cells.get(idx) or {}
        merged_cell = merged_cells.get(idx) or live_cells.get(idx) or {}
        live_sig = _cell_sig(live_cell)
        merged_sig = _cell_sig(merged_cell)
        rev_sig = _revision_sig(revision_rows.get(str(idx)) or {})
        live_prev[idx] = dict(live_sig)
        merged_prev[idx] = dict(merged_sig)
        rev_prev[idx] = dict(rev_sig)
        order = _best_order(live_sig, merged_sig, rev_sig)
        title = _best_title(live_sig, merged_sig, rev_sig)
        if order is not None and _title_is_executed(title):
            reported_pairs.add((idx, order))
            session_global_max = max(session_global_max, order)
    return session_global_max


def _scan_global_new_runs(
    *,
    live_cells: dict[int, dict],
    merged_cells: dict[int, dict],
    revision_rows: dict[str, dict],
    live_prev: dict[int, dict],
    merged_prev: dict[int, dict],
    reported_pairs: set[tuple[int, int]],
    session_global_max: int,
) -> list[tuple[int, dict, dict, list[str]]]:
    """
    Catch runs via global execution counter — even if we miss intermediate polls.
    Each new (cell_index, execution_order) pair above the session watermark is a run.
    """
    out: list[tuple[int, dict, dict, list[str]]] = []
    tick_global_max = session_global_max

    for idx in sorted(set(live_cells) | set(merged_cells)):
        live_cell = live_cells.get(idx) or merged_cells.get(idx)
        merged_cell = merged_cells.get(idx) or live_cells.get(idx)
        if not live_cell:
            continue

        live_cur = _cell_sig(live_cell)
        merged_cur = _cell_sig(merged_cell or live_cell)
        rev_cur = _revision_sig(revision_rows.get(str(idx)) or {})
        order = _best_order(live_cur, merged_cur, rev_cur)
        if order is not None:
            tick_global_max = max(tick_global_max, order)

        pair = (idx, order) if order is not None else None
        if pair is None or pair in reported_pairs:
            continue

        title = _best_title(live_cur, merged_cur, rev_cur)
        lp = live_prev.get(idx, live_cur)
        mp = merged_prev.get(idx, merged_cur)
        cell_reported_max = max((o for i, o in reported_pairs if i == idx), default=-1)

        reasons: list[str] = []
        if order > session_global_max:
            reasons.append(f"global_order={order}")
        if order > cell_reported_max:
            reasons.append(f"cell_order={order}")
        if _title_is_executed(title):
            reasons.append("executed_title")
        if live_cur.get("output_hash") != lp.get("output_hash"):
            reasons.append("output_changed")
        if live_cur.get("execution_status") in ("running", "queued"):
            reasons.append(live_cur["execution_status"])
        if rev_cur.get("seen_running"):
            reasons.append("revision_running")

        # Accept if: new global order, or cell order bumped, or output changed while running
        qualifies = (
            order > session_global_max
            or order > cell_reported_max
            or (
                live_cur.get("output_hash") != lp.get("output_hash")
                and live_cur.get("execution_status") in ("running", "queued", "executed")
            )
            or (
                _title_is_executed(title)
                and title != _best_title(mp, lp, {})
            )
        )
        if qualifies and reasons:
            out.append((idx, live_cur, merged_cur, reasons))

    return out


def _detect_run(
    *,
    idx: int,
    live_prev: dict,
    live_cur: dict,
    merged_prev: dict,
    merged_cur: dict,
    rev_prev: dict,
    rev_cur: dict,
    session_reported_order: dict[int, int],
) -> list[str]:
    """Live JSON for fast output/status; merged + revision for execution title/order."""
    reasons: list[str] = []

    if live_cur.get("execution_status") == "running" and live_prev.get("execution_status") != "running":
        reasons.append("running")
    if live_cur.get("execution_status") == "queued" and live_prev.get("execution_status") not in ("queued", "running"):
        reasons.append("queued")

    cur_title = merged_cur.get("execution_title") or live_cur.get("execution_title") or ""
    prev_title = merged_prev.get("execution_title") or live_prev.get("execution_title") or ""
    if _title_is_executed(cur_title) and cur_title != prev_title:
        reasons.append("executed_title")

    cur_order = merged_cur.get("execution_order")
    if cur_order is None:
        cur_order = live_cur.get("execution_order")
    if cur_order is None:
        cur_order = rev_cur.get("execution_order")

    prev_order = merged_prev.get("execution_order")
    if prev_order is None:
        prev_order = live_prev.get("execution_order")
    if prev_order is None:
        prev_order = rev_prev.get("execution_order")

    if cur_order is not None and (prev_order is None or cur_order > prev_order):
        reasons.append(f"order={cur_order}")

    last_reported = session_reported_order.get(idx, -1)
    if cur_order is not None and cur_order > last_reported:
        if f"order={cur_order}" not in reasons:
            reasons.append(f"new_order={cur_order}")

    if live_cur.get("output_hash") != live_prev.get("output_hash"):
        reasons.append("output_changed")

    if rev_cur.get("seen_running") and not rev_prev.get("seen_running"):
        reasons.append("revision_running")

    rev_title = str(rev_cur.get("execution_title") or "")
    rev_prev_title = str(rev_prev.get("execution_title") or "")
    if _title_is_executed(rev_title) and rev_title != rev_prev_title:
        reasons.append("revision_title")

    rev_order = rev_cur.get("execution_order")
    rev_prev_order = rev_prev.get("execution_order")
    if rev_order is not None and (rev_prev_order is None or rev_order > rev_prev_order):
        if f"order={rev_order}" not in reasons and f"session_order={rev_order}" not in reasons:
            reasons.append(f"revision_order={rev_order}")

    return reasons


def _display_sig(live: dict, merged: dict) -> dict:
    """Prefer merged execution fields; live for output preview."""
    out = dict(merged)
    if live.get("output_preview"):
        out["output_preview"] = live["output_preview"]
        out["output_hash"] = live["output_hash"]
    if not _title_is_executed(str(out.get("execution_title") or "")):
        lt = live.get("execution_title") or ""
        if _title_is_executed(lt):
            out["execution_title"] = lt
    if out.get("execution_order") is None and live.get("execution_order") is not None:
        out["execution_order"] = live["execution_order"]
    if live.get("execution_status") in ("running", "queued"):
        out["execution_status"] = live["execution_status"]
    return out


def _print_run(*, url: str, idx: int, sig: dict, reasons: list[str], source: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 72)
    print(f"[{now}] CELL RUN  ({', '.join(reasons)})")
    print(f"URL: {url}")
    print(f"Kernel: ON  |  Snapshot: {source}")
    print(f"Cell: {idx}")
    order = sig.get("execution_order")
    print(f"Execution order: {order if order is not None else '-'}")
    print(f"Execution title: {sig.get('execution_title') or '-'}")
    print(f"Execution status: {sig.get('execution_status') or '-'}")
    if sig.get("input_preview"):
        print(f"Input: {sig['input_preview']}")
    if sig.get("output_preview"):
        print(f"Output: {sig['output_preview']}")
    print("=" * 72)


def _load_sources_burst(url: str, *, retries: int = 5) -> tuple[dict | None, dict | None, str]:
    """Re-read quickly after a file change — catches lagging legacy writes."""
    last = _load_sources(url)
    for _ in range(max(0, retries - 1)):
        time.sleep(0.05)
        last = _load_sources(url)
    return last


def _emit_run(
    *,
    url: str,
    idx: int,
    order: int | None,
    live_cur: dict,
    merged_cur: dict,
    reasons: list[str],
    source: str,
    reported_pairs: set[tuple[int, int]],
    session_global_max: list[int],
    verbose: bool,
) -> None:
    display = _display_sig(live_cur, merged_cur)
    if order is None:
        order = display.get("execution_order")
    if isinstance(order, int):
        pair = (idx, order)
        if pair in reported_pairs:
            return
        reported_pairs.add(pair)
        session_global_max[0] = max(session_global_max[0], order)
    if verbose:
        print(f"[debug] cell {idx} order={order} reasons={reasons}")
    _print_run(url=url, idx=idx, sig=display, reasons=reasons, source=source)


def monitor(url: str, *, interval: float = 0.15, verbose: bool = False) -> None:
    print(f"Cell run watcher: {url}")
    print(f"Fast poll {interval}s (bumps to 80ms after file activity) | Ctrl+C to stop\n")

    watch_paths = _paths_for_url(url)
    prev_mtimes = _file_mtouches(watch_paths)
    last_activity = 0.0
    prev_scenario: str | None = None
    live_prev: dict[int, dict] = {}
    merged_prev: dict[int, dict] = {}
    rev_prev: dict[int, dict] = {}
    reported_pairs: set[tuple[int, int]] = set()
    session_global_max = [0]
    missing_warned = False
    tracking = False
    host_log_pos = 0
    if HOST_LOG_PATH.is_file():
        try:
            host_log_pos = HOST_LOG_PATH.stat().st_size
        except OSError:
            host_log_pos = 0

    while True:
        mtimes = _file_mtouches(watch_paths)
        files_changed = mtimes != prev_mtimes
        prev_mtimes = mtimes
        if files_changed:
            last_activity = time.time()

        burst = files_changed or (time.time() - last_activity < 2.5)
        if burst:
            live_nb, merged_nb, source = _load_sources_burst(url)
        else:
            live_nb, merged_nb, source = _load_sources(url)
        notebook = merged_nb or live_nb
        if not isinstance(notebook, dict):
            if not missing_warned:
                print("[WARN] No notebook JSON yet — waiting for scrape…")
                missing_warned = True
            time.sleep(interval)
            continue
        missing_warned = False

        raw_scenario = _normalize_scenario(str(notebook.get("kernelScenario") or "unknown"))
        scenario = raw_scenario
        if raw_scenario == SCENARIO_LOADING and prev_scenario:
            scenario = _normalize_scenario(prev_scenario)

        cells = _code_cells(notebook)
        live_cells = _code_cells(live_nb or {})
        merged_cells = _code_cells(merged_nb or notebook)
        if not cells and not live_cells:
            time.sleep(interval)
            continue

        if raw_scenario == SCENARIO_LOADING:
            time.sleep(interval)
            continue

        if prev_scenario is None:
            prev_scenario = scenario
            if _scenario_is_on(scenario):
                revision_rows = _revision_cells(url)
                session_global_max[0] = _init_session_state(
                    live_cells, merged_cells, revision_rows,
                    live_prev, merged_prev, rev_prev, reported_pairs,
                )
                tracking = True
                n = len(set(live_cells) | set(merged_cells))
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Kernel ON — "
                    f"tracking {n} cells\n"
                )
            elif _scenario_is_off(scenario):
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Kernel OFF — "
                    f"start kernel to track runs\n"
                )
            time.sleep(interval)
            continue

        if _scenario_is_off(scenario) and _scenario_is_on(prev_scenario):
            live_prev.clear()
            merged_prev.clear()
            rev_prev.clear()
            reported_pairs.clear()
            session_global_max[0] = 0
            tracking = False
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Kernel OFF — run tracking paused\n")

        if _scenario_is_on(scenario) and _scenario_is_off(prev_scenario):
            revision_rows = _revision_cells(url)
            reported_pairs.clear()
            session_global_max[0] = _init_session_state(
                live_cells, merged_cells, revision_rows,
                live_prev, merged_prev, rev_prev, reported_pairs,
            )
            tracking = True
            n = len(set(live_cells) | set(merged_cells))
            print(
                f"\n[{datetime.now().strftime('%H:%M:%S')}] Kernel ON — "
                f"tracking {n} cells\n"
            )

        revision_rows = _revision_cells(url)

        if tracking and _scenario_is_on(scenario):
            # Host log often updates before legacy JSON — cheap extra signal.
            if HOST_LOG_PATH.is_file():
                try:
                    size = HOST_LOG_PATH.stat().st_size
                    if size > host_log_pos:
                        chunk = HOST_LOG_PATH.read_text(encoding="utf-8", errors="replace")[host_log_pos:size]
                        host_log_pos = size
                        for cell_idx, order in _parse_host_log_exec(chunk):
                            if (cell_idx, order) in reported_pairs:
                                continue
                            cell = merged_cells.get(cell_idx) or live_cells.get(cell_idx)
                            if not cell:
                                continue
                            live_cur = _cell_sig(cell)
                            merged_cur = _cell_sig(cell)
                            merged_cur["execution_order"] = order
                            if not _title_is_executed(merged_cur.get("execution_title") or ""):
                                merged_cur["execution_title"] = f"Cell executed (Execution #{order})"
                            _emit_run(
                                url=url, idx=cell_idx, order=order,
                                live_cur=live_cur, merged_cur=merged_cur,
                                reasons=[f"host_log order={order}"], source="host.log",
                                reported_pairs=reported_pairs,
                                session_global_max=session_global_max,
                                verbose=verbose,
                            )
                except OSError:
                    pass

            # Primary: global execution-order scan (catches batched/missed polls).
            for idx, live_cur, merged_cur, reasons in _scan_global_new_runs(
                live_cells=live_cells,
                merged_cells=merged_cells,
                revision_rows=revision_rows,
                live_prev=live_prev,
                merged_prev=merged_prev,
                reported_pairs=reported_pairs,
                session_global_max=session_global_max[0],
            ):
                order = _best_order(live_cur, merged_cur, _revision_sig(revision_rows.get(str(idx)) or {}))
                _emit_run(
                    url=url, idx=idx, order=order,
                    live_cur=live_cur, merged_cur=merged_cur,
                    reasons=reasons, source=source,
                    reported_pairs=reported_pairs,
                    session_global_max=session_global_max,
                    verbose=verbose,
                )

            for idx in sorted(set(live_cells) | set(merged_cells)):
                live_cell = live_cells.get(idx) or merged_cells.get(idx)
                merged_cell = merged_cells.get(idx) or live_cells.get(idx)
                if not live_cell:
                    continue
                live_cur = _cell_sig(live_cell)
                merged_cur = _cell_sig(merged_cell or live_cell)
                lp = live_prev.get(idx, live_cur)
                mp = merged_prev.get(idx, merged_cur)
                rp = rev_prev.get(idx, {})
                rc = _revision_sig(revision_rows.get(str(idx)) or {})

                reasons = _detect_run(
                    idx=idx,
                    live_prev=lp,
                    live_cur=live_cur,
                    merged_prev=mp,
                    merged_cur=merged_cur,
                    rev_prev=rp,
                    rev_cur=rc,
                    session_reported_order={idx: max((o for i, o in reported_pairs if i == idx), default=-1)},
                )
                if reasons:
                    order = _best_order(live_cur, merged_cur, rc)
                    _emit_run(
                        url=url, idx=idx, order=order,
                        live_cur=live_cur, merged_cur=merged_cur,
                        reasons=reasons, source=source,
                        reported_pairs=reported_pairs,
                        session_global_max=session_global_max,
                        verbose=verbose,
                    )

                live_prev[idx] = live_cur
                merged_prev[idx] = merged_cur
                rev_prev[idx] = rc

        prev_scenario = scenario
        if tracking and burst:
            time.sleep(min(interval, 0.05))
        else:
            time.sleep(interval)


def main() -> None:
    import os

    if os.environ.get("KERNEL_EXECUTION_METADATA_ENABLED", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        raise SystemExit(
            "Kernel execution metadata is disabled. "
            "Set KERNEL_EXECUTION_METADATA_ENABLED=1 to enable cell-run monitoring."
        )
    parser = argparse.ArgumentParser(description="Watch notebook JSON and print cell runs.")
    parser.add_argument("notebook", nargs="?", default="", help="URL or short name (e.g. testing-ol)")
    parser.add_argument("--url", default="", help="Notebook URL")
    parser.add_argument("--interval", type=float, default=0.15, help="Poll interval seconds (default 0.15)")
    parser.add_argument("--verbose", action="store_true", help="Print debug transitions")
    args = parser.parse_args()

    url = (args.url or args.notebook or "").strip()
    if not url:
        parser.error("Provide notebook URL or short name")
    if not url.startswith("http"):
        url = _resolve_notebook_url(url)

    try:
        monitor(url, interval=max(0.05, float(args.interval)), verbose=args.verbose)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
