"""Watch persistent notebook JSON; print insert / delete / edit via data-uuid."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from typing import Any

try:
    from .bot_tool_utils import pick_notebook_url
    from .persistent_notebook_verify import (
        load_persistent_notebook_snapshot,
        notebook_structure_hash,
        persistent_snapshot_path,
    )
    from .uuid_cell_diff import cell_uuid, diff_cells, ordered_cells, uuid_coverage
except Exception:
    from bot_tool_utils import pick_notebook_url  # type: ignore
    from persistent_notebook_verify import (  # noqa: E402
        load_persistent_notebook_snapshot,
        notebook_structure_hash,
        persistent_snapshot_path,
    )
    from uuid_cell_diff import cell_uuid, diff_cells, ordered_cells, uuid_coverage  # type: ignore

WATCH_POLL_SEC = float(os.environ.get("NOTEBOOK_JSON_WATCH_POLL_SEC", "0.1"))
WATCH_SETTLE_MS = int(os.environ.get("NOTEBOOK_JSON_WATCH_SETTLE_MS", "200"))
STRUCTURAL_QUIESCE_MS = int(os.environ.get("NOTEBOOK_JSON_WATCH_STRUCTURAL_QUIESCE_MS", "1200"))

_last_emit_mono: float | None = None
_edit_line_index: int | None = None
_last_line_len: int = 0
_last_line_rows: int = 0


def _enable_vt_on_windows() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


_enable_vt_on_windows()


def _term_cols() -> int:
    try:
        return max(1, shutil.get_terminal_size().columns)
    except OSError:
        return 80


def _line_rows(n_chars: int) -> int:
    return max(1, (n_chars + _term_cols() - 1) // _term_cols())


def _cells_by_index(data: dict | None) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not isinstance(data, dict):
        return out
    for cell in data.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        try:
            out[int(cell.get("index"))] = cell
        except (TypeError, ValueError):
            continue
    return out


def _finalize_edit_line() -> None:
    global _edit_line_index, _last_line_len, _last_line_rows
    if _edit_line_index is not None:
        sys.stdout.write("\n")
        sys.stdout.flush()
        _edit_line_index = None
        _last_line_len = 0
        _last_line_rows = 0


def _write_edit_line(line: str, *, overwrite: bool) -> None:
    global _last_line_len, _last_line_rows
    if overwrite:
        for _ in range(max(0, _last_line_rows - 1)):
            sys.stdout.write("\033[A")
        pad = max(0, _last_line_len - len(line))
        sys.stdout.write("\r\033[2K" + line + (" " * pad))
        new_rows = _line_rows(len(line))
        if new_rows < _last_line_rows:
            for _ in range(_last_line_rows - new_rows):
                sys.stdout.write("\n\033[2K")
            for _ in range(_last_line_rows - new_rows):
                sys.stdout.write("\033[A")
    else:
        sys.stdout.write(line)
        new_rows = _line_rows(len(line))
    sys.stdout.flush()
    _last_line_len = len(line)
    _last_line_rows = new_rows


def _emit_change(action: str, index: int, *, text: str | None = None, uuid: str | None = None) -> None:
    global _last_emit_mono, _edit_line_index
    payload: dict[str, Any] = {"action": action, "index": index}
    if uuid:
        payload["uuid"] = uuid
    if action == "edited" and text is not None:
        payload["text"] = text
    line = json.dumps(payload, ensure_ascii=False)
    _last_emit_mono = time.monotonic()

    if action == "edited":
        if _edit_line_index == index:
            _write_edit_line(line, overwrite=True)
        else:
            _finalize_edit_line()
            _edit_line_index = index
            _write_edit_line(line, overwrite=False)
        return

    _finalize_edit_line()
    print(line, flush=True)


def _print_status(*, action: str, url: str, path, cells: dict[int, dict], struct_hash: str = "") -> None:
    rows = ordered_cells(cells.values())
    cov = uuid_coverage(rows)
    payload: dict[str, Any] = {
        "action": action,
        "url": url,
        "path": str(path) if path else "",
        "cell_count": len(cells),
        "structure_hash": struct_hash,
        "uuid_coverage": round(cov, 3),
    }
    if cov < 1.0:
        payload["hint"] = "Reload extension + refresh notebook so every cell has data-uuid"
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def diff_cells_by_index(before: dict[int, dict], after: dict[int, dict]) -> list[dict[str, Any]]:
    return diff_cells(ordered_cells(before.values()), ordered_cells(after.values()))


def diff_and_print(before: dict[int, dict], after: dict[int, dict]) -> list[dict[str, Any]]:
    events = diff_cells_by_index(before, after)
    for ev in events:
        _emit_change(ev["action"], int(ev["index"]), text=ev.get("text"), uuid=ev.get("uuid"))
    return events


def _wait_settled(path, mtime: float, settle_ms: int) -> float:
    deadline = time.monotonic() + max(0.05, settle_ms / 1000.0)
    latest = mtime
    while time.monotonic() < deadline:
        time.sleep(0.05)
        try:
            cur = path.stat().st_mtime
        except OSError:
            break
        if cur != latest:
            latest = cur
            deadline = time.monotonic() + max(0.05, settle_ms / 1000.0)
    return latest


def _load_cells(url: str) -> tuple[dict[int, dict], str]:
    data, _source = load_persistent_notebook_snapshot(url)
    return _cells_by_index(data), notebook_structure_hash(data)


def _wait_quiescence(url: str, path, *, quiesce_ms: int) -> tuple[dict[int, dict], str, float]:
    interval = max(0.05, WATCH_POLL_SEC)
    quiesce_sec = max(0.2, quiesce_ms / 1000.0)
    last_change = time.monotonic()
    latest_cells: dict[int, dict] = {}
    latest_hash = ""
    latest_mtime = -1.0

    while True:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = latest_mtime
        cells, struct_hash = _load_cells(url)
        if mtime != latest_mtime or struct_hash != latest_hash or len(cells) != len(latest_cells):
            latest_mtime = mtime
            latest_hash = struct_hash
            latest_cells = cells
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= quiesce_sec:
            break
        time.sleep(interval)

    return latest_cells, latest_hash, latest_mtime


def watch_persistent_json(url: str, *, timeout: float = 0, poll_sec: float | None = None) -> dict[str, Any]:
    interval = max(0.05, float(poll_sec or WATCH_POLL_SEC))
    path = persistent_snapshot_path(url)

    last_mtime = -1.0
    last_cells: dict[int, dict] = {}
    last_struct_hash = ""
    baseline_ready = False
    deadline = (time.monotonic() + float(timeout)) if float(timeout) > 0 else None
    total_events = 0

    try:
        while deadline is None or time.monotonic() < deadline:
            if path is None or not path.is_file():
                time.sleep(interval)
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                time.sleep(interval)
                continue

            if baseline_ready and mtime == last_mtime:
                time.sleep(interval)
                continue

            if baseline_ready:
                mtime = _wait_settled(path, mtime, WATCH_SETTLE_MS)

            after, struct_hash = _load_cells(url)

            if not baseline_ready:
                last_cells = after
                last_struct_hash = struct_hash
                last_mtime = mtime
                baseline_ready = True
                _print_status(action="watch_start", url=url, path=path, cells=after, struct_hash=struct_hash)
                _print_status(action="baseline_ready", url=url, path=path, cells=after, struct_hash=struct_hash)
                time.sleep(interval)
                continue

            if struct_hash == last_struct_hash:
                last_mtime = mtime
                time.sleep(interval)
                continue

            anchor = dict(last_cells)
            if len(after) != len(anchor):
                after, struct_hash, mtime = _wait_quiescence(url, path, quiesce_ms=STRUCTURAL_QUIESCE_MS)

            events = diff_cells_by_index(anchor, after)
            for ev in events:
                _emit_change(ev["action"], int(ev["index"]), text=ev.get("text"), uuid=ev.get("uuid"))
            total_events += len(events)

            last_cells = after
            last_struct_hash = struct_hash
            last_mtime = mtime
            time.sleep(interval)
    except KeyboardInterrupt:
        _finalize_edit_line()
        return {"ok": True, "events": total_events, "stopped": "interrupted"}

    return {"ok": True, "events": total_events, "stopped": "timeout"}


def run_watch_notebook_json(args: dict) -> dict:
    url = pick_notebook_url(args)
    if not url:
        return {"ok": False, "tool": "watch_notebook_json", "error": "url is required"}
    try:
        timeout = float(args.get("timeout") or 0)
    except (TypeError, ValueError):
        timeout = 0.0
    result = watch_persistent_json(url, timeout=timeout)
    result["tool"] = "watch_notebook_json"
    result["url"] = url
    return result
