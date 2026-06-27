"""Verify run_cell via execution signals — not extension dispatch ack."""

from __future__ import annotations

import os
import time
from typing import Any

RUN_VERIFY_TIMEOUT_SEC = float(os.environ.get("RUN_CELL_VERIFY_TIMEOUT_SEC", "30"))
RUN_VERIFY_FAST_POLL_SEC = float(os.environ.get("RUN_CELL_VERIFY_POLL_SEC", "0.05"))
RUN_DISPATCH_TIMEOUT_SEC = float(os.environ.get("RUN_CELL_DISPATCH_TIMEOUT_SEC", "2.5"))
EXECUTION_TIME_POLL_SEC = float(os.environ.get("RUN_CELL_EXECUTION_TIME_POLL_SEC", "2.5"))
EXECUTION_TITLE_SETTLE_SEC = float(os.environ.get("RUN_CELL_EXECUTION_TITLE_SETTLE_SEC", "0"))
EXECUTION_TITLE_MAX_WAIT_MS = int(os.environ.get("RUN_CELL_EXECUTION_TITLE_MAX_WAIT_MS", "2000"))


def _host_log_size() -> int:
    try:
        from .config import LOG_PATH
    except Exception:
        from config import LOG_PATH  # type: ignore
    try:
        return LOG_PATH.stat().st_size if LOG_PATH.is_file() else 0
    except OSError:
        return 0


def _read_cell_execution_title(url: str, cell_index: int, tab_id: int | None = None) -> str:
    try:
        from .cell_execution_observer import normalize_execution_title
    except Exception:
        from cell_execution_observer import normalize_execution_title  # type: ignore

    browser_title = _read_cell_execution_title_browser(url, cell_index, tab_id)
    if browser_title:
        return browser_title

    try:
        from .cell_structure_live import read_live_execution_cells
    except Exception:
        from cell_structure_live import read_live_execution_cells  # type: ignore
    try:
        from .notebook_context import load_notebook_snapshot
    except Exception:
        from notebook_context import load_notebook_snapshot  # type: ignore

    try:
        live_cells, _meta = read_live_execution_cells(url)
        for cell in live_cells:
            if getattr(cell, "index", None) == cell_index:
                title = normalize_execution_title(str(getattr(cell, "execution_title", "") or ""))
                if title:
                    return title
    except Exception:
        pass

    data, _source = load_notebook_snapshot(url)
    row = _cell_by_index(data if isinstance(data, dict) else None, cell_index)
    return normalize_execution_title(str((row or {}).get("execution_title") or ""))


def _read_cell_execution_title_browser(url: str, cell_index: int, tab_id: int | None) -> str:
    import uuid

    try:
        from .bot_command import execute_bot_command
        from .cell_execution_observer import normalize_execution_title
        from .cell_index import app_to_dom
    except Exception:
        from bot_command import execute_bot_command  # type: ignore
        from cell_execution_observer import normalize_execution_title  # type: ignore
        from cell_index import app_to_dom  # type: ignore

    dom_index = app_to_dom(int(cell_index))
    if dom_index is None:
        return ""

    cmd: dict[str, Any] = {
        "action": "get_cell_execution_title",
        "url": url,
        "cell_index": int(cell_index),
        "cellIndex": dom_index,
        "index_basis": "app",
        "requestId": str(uuid.uuid4()),
        "timeout": max(5.0, EXECUTION_TITLE_MAX_WAIT_MS / 1000.0 + 2.5),
        "maxWaitMs": EXECUTION_TITLE_MAX_WAIT_MS,
    }
    if isinstance(tab_id, int) and tab_id > 0:
        cmd["tab_id"] = tab_id
    elif not tab_id:
        try:
            from .browser_target_context import resolve_tab_id_for_url
        except Exception:
            from browser_target_context import resolve_tab_id_for_url  # type: ignore
        resolved = resolve_tab_id_for_url(url)
        if isinstance(resolved, int) and resolved > 0:
            cmd["tab_id"] = resolved

    try:
        event = execute_bot_command(cmd, timeout=float(cmd["timeout"]))
    except Exception:
        return ""

    if not event.get("ok"):
        return ""

    inner = event.get("result") if isinstance(event.get("result"), dict) else {}
    payload = inner
    if isinstance(inner.get("result"), dict):
        payload = inner["result"]
    return normalize_execution_title(
        str(payload.get("execution_title") or payload.get("raw_title") or "")
    )


def _timing_from_title(title: str, *, before_title: str = "") -> dict[str, Any] | None:
    try:
        from .cell_execution_observer import normalize_execution_title, parse_execution_time_sec
    except Exception:
        from cell_execution_observer import normalize_execution_title, parse_execution_time_sec  # type: ignore

    normalized = normalize_execution_title(title)
    sec = parse_execution_time_sec(normalized)
    if sec is None or not normalized:
        return None
    if before_title and normalized == normalize_execution_title(before_title):
        before_sec = parse_execution_time_sec(before_title)
        if before_sec is not None and sec == before_sec:
            return None
    return {
        "execution_time_sec": sec,
        "execution_title": normalized,
    }


def _poll_execution_time_sec(
    url: str,
    cell_index: int,
    *,
    before_title: str = "",
    timeout: float = EXECUTION_TIME_POLL_SEC,
    tab_id: int | None = None,
    host_log_offset: int = 0,
) -> dict[str, Any]:
    """Wait briefly for PROMPT_SIGNAL timing in host.log (non-blocking for dispatch)."""
    try:
        from .cell_structure_live import read_host_log_prompt_signals
    except Exception:
        from cell_structure_live import read_host_log_prompt_signals  # type: ignore

    settle = max(0.0, float(EXECUTION_TITLE_SETTLE_SEC))
    if settle > 0:
        time.sleep(settle)

    deadline = time.monotonic() + max(0.2, float(timeout))
    log_offset = int(host_log_offset or 0)
    idx = int(cell_index)

    while time.monotonic() < deadline:
        pairs, log_offset = read_host_log_prompt_signals(log_offset, cell_index=idx)
        for _ci, text in pairs:
            hit = _timing_from_title(text, before_title=before_title)
            if hit:
                return hit
        time.sleep(max(0.05, RUN_VERIFY_FAST_POLL_SEC))

    return {
        "execution_time_sec": None,
        "execution_title": None,
    }


def _cell_by_index(data: dict | None, cell_index: int) -> dict | None:
    if not isinstance(data, dict):
        return None
    for cell in data.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        try:
            if int(cell.get("index")) == int(cell_index):
                return cell
        except (TypeError, ValueError):
            continue
    return None


def capture_run_baseline(url: str, cell_index: int) -> dict[str, Any]:
    """Snapshot + host.log offset before dispatch (for fast EXEC DETECTED matching)."""
    try:
        from .notebook_context import load_notebook_snapshot
    except Exception:
        from notebook_context import load_notebook_snapshot  # type: ignore

    data, source = load_notebook_snapshot(url)
    return {
        "url": url,
        "cell_index": int(cell_index),
        "snapshot": data if isinstance(data, dict) else {},
        "snapshot_source": source,
        "before_cell": _cell_by_index(data if isinstance(data, dict) else None, cell_index),
        "host_log_offset": _host_log_size(),
    }


def dispatch_run_cell(cmd: dict, *, timeout: float = RUN_DISPATCH_TIMEOUT_SEC) -> dict[str, Any]:
    """Fire run in browser; do not wait for frame/extension ack."""
    import uuid

    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command  # type: ignore

    attempt = dict(cmd)
    attempt["requestId"] = str(attempt.get("requestId") or uuid.uuid4())
    attempt["fire_and_forget"] = True
    attempt["wait_for_result"] = False
    attempt["timeout"] = min(float(timeout), float(attempt.get("timeout") or timeout))
    return execute_bot_command(attempt, timeout=float(timeout))


def wait_for_run_verification(
    url: str,
    cell_index: int,
    before_data: dict | None,
    *,
    before_cell: dict | None = None,
    host_log_offset: int = 0,
    timeout: float = RUN_VERIFY_TIMEOUT_SEC,
    started_at: float | None = None,
    tab_id: int | None = None,
) -> dict[str, Any]:
    """
    Confirm the cell actually ran using low-latency signals:
      1. host.log EXEC DETECTED (fastest — ms after scrape)
      2. live/revision execution observer + cell_run_snapshot
    """
    try:
        from .cell_run_snapshot import detect_run_verification
    except Exception:
        from cell_run_snapshot import detect_run_verification  # type: ignore
    try:
        from .cell_execution_observer import verify_cell_ran
    except Exception:
        from cell_execution_observer import verify_cell_ran  # type: ignore
    try:
        from .cell_structure_live import read_host_log_exec_lines, read_host_log_prompt_signals
    except Exception:
        from cell_structure_live import read_host_log_exec_lines, read_host_log_prompt_signals  # type: ignore
    try:
        from .notebook_context import load_notebook_snapshot
    except Exception:
        from notebook_context import load_notebook_snapshot  # type: ignore

    idx = int(cell_index)
    before_cells = (before_data or {}).get("cells") or []
    if before_cell is None:
        before_cell = _cell_by_index(before_data, idx)

    before_title = str((before_cell or {}).get("execution_title") or "").strip()
    deadline = time.monotonic() + max(0.5, float(timeout))
    log_offset = int(host_log_offset or 0)
    prompt_log_offset = int(host_log_offset or 0)
    last_error = f"run not verified for cell {idx} within {timeout}s"
    seen_exec_orders: set[tuple[int, int]] = set()
    captured_timing: dict[str, Any] = {}

    def _verified(**fields: Any) -> dict[str, Any]:
        timing = captured_timing or _poll_execution_time_sec(
            url,
            idx,
            before_title=before_title,
            tab_id=tab_id,
            host_log_offset=int(host_log_offset or 0),
        )
        return {
            "ok": True,
            "run_verified": True,
            "cell_index": idx,
            **fields,
            **timing,
        }

    while time.monotonic() < deadline:
        prompt_pairs, prompt_log_offset = read_host_log_prompt_signals(prompt_log_offset, cell_index=idx)
        for _ci, text in prompt_pairs:
            hit = _timing_from_title(text, before_title=before_title)
            if hit:
                captured_timing = hit

        pairs, log_offset = read_host_log_exec_lines(log_offset)
        for ci, order in pairs:
            key = (ci, order)
            if key in seen_exec_orders:
                continue
            seen_exec_orders.add(key)
            if ci != idx:
                continue
            return _verified(execution_order=order, wait_reason="exec_detected_log")

        if captured_timing:
            return _verified(wait_reason="prompt_signal")

        try:
            live_data, _live_source = load_notebook_snapshot(url)
            live_raw = (live_data or {}).get("cells") if isinstance(live_data, dict) else []
            if not isinstance(live_raw, list):
                live_raw = []
            observer = verify_cell_ran(before_cells, live_raw, idx)
            if observer.get("verified"):
                return _verified(
                    execution_order=observer.get("execution_order"),
                    wait_reason="execution_observer_live",
                )
        except Exception:
            pass

        data, source = load_notebook_snapshot(url)
        after_cell = _cell_by_index(data, idx)
        if after_cell:
            detection = detect_run_verification(before_cell, after_cell)
            observer = verify_cell_ran(before_cells, (data or {}).get("cells") or [], idx)
            if detection.get("run_verified") or observer.get("verified"):
                reasons: list[str] = []
                if detection.get("execution_order_increased"):
                    reasons.append("execution_order_increased")
                if detection.get("snapshot_changed"):
                    reasons.append("snapshot_changed")
                if observer.get("verified"):
                    reasons.append("execution_observer")
                return _verified(
                    execution_order=detection.get("execution_order") or observer.get("execution_order"),
                    wait_reason="+".join(dict.fromkeys(reasons)) or "snapshot_changed",
                )

        time.sleep(max(0.01, RUN_VERIFY_FAST_POLL_SEC))

    return {
        "ok": False,
        "error": last_error,
        "cell_index": idx,
        "run_verified": False,
    }
