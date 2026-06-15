"""Fast execution signals from extension PROMPT_SIGNAL — patch live JSON + execution_state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    from .kernel_execution_policy import TITLE_PENDING, TITLE_RUNNING
    from .persistence_helpers import (
        _load_execution_state,
        _save_execution_state,
        get_safe_filename,
        read_json_file,
    )
    from .persistence_helpers import _atomic_write_json as atomic_write_json
except Exception:
    from kernel_execution_policy import TITLE_PENDING, TITLE_RUNNING
    from persistence_helpers import (
        _load_execution_state,
        _save_execution_state,
        get_safe_filename,
        read_json_file,
    )
    from persistence_helpers import _atomic_write_json as atomic_write_json

from pathlib import Path

_DATA_ROOT = Path(__file__).parent / "data"
_LIVE_DIR = _DATA_ROOT / "notebooks" / "live"


def classify_prompt_phase(text: str) -> str:
    t = str(text or "").strip().lower()
    if not t:
        return "unknown"
    if "queued" in t or "pending" in t:
        return "queued"
    if "started execution" in t or "being executed" in t:
        return "running"
    if "executed" in t:
        return "executed"
    return "unknown"


def _normalized_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _find_or_create_code_cell(cells: list, cell_index: int) -> dict[str, Any]:
    for cell in cells:
        if isinstance(cell, dict) and cell.get("index") == cell_index:
            return cell
    row: dict[str, Any] = {
        "type": "code",
        "index": cell_index,
        "input": "",
        "output": "",
        "execution_status": "idle",
    }
    cells.append(row)
    return row


def patch_prompt_execution_signal(
    cell_index: int,
    tab_url: str,
    text: str,
    *,
    exec_order: Optional[int] = None,
    exec_ts: Optional[int] = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Write running/executed state immediately so monitor sees mtime change within ~ms."""
    try:
        cell_index = int(cell_index)
    except (TypeError, ValueError):
        return False
    if cell_index <= 0:
        return False

    tab_url = _normalized_url(tab_url)
    if not tab_url:
        return False

    phase = classify_prompt_phase(text)
    if phase == "unknown":
        return False

    emit = log or (lambda _msg: None)
    now_iso = datetime.now(timezone.utc).isoformat()

    live_path = _LIVE_DIR / get_safe_filename(tab_url)
    data = read_json_file(live_path) or {"tabUrl": tab_url, "title": "notebook", "cells": []}
    cells = data.get("cells")
    if not isinstance(cells, list):
        cells = []
        data["cells"] = cells

    target = _find_or_create_code_cell(cells, cell_index)
    target["type"] = "code"
    target["index"] = cell_index

    if exec_order is not None:
        try:
            target["execution_order"] = int(exec_order)
        except (TypeError, ValueError):
            pass

    if phase == "queued":
        target["execution_status"] = "queued"
        target["execution_title"] = TITLE_PENDING
    elif phase == "running":
        target["execution_status"] = "running"
        target["execution_title"] = TITLE_RUNNING
    else:
        target["execution_status"] = "executed"
        order = target.get("execution_order")
        if order is not None:
            target["execution_title"] = f"Cell executed (Execution #{int(order)})"
        else:
            target["execution_title"] = str(text or "").strip() or "Cell executed"

    if exec_ts:
        target["execution_signal_ts"] = int(exec_ts)

    data["lastUpdated"] = now_iso
    data["tabUrl"] = tab_url
    _LIVE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(live_path, data)

    state = _load_execution_state()
    nb = state.get(tab_url)
    if not isinstance(nb, dict):
        nb = {
            "active_revision": "",
            "revisions": {},
            "last_seen_at": now_iso,
            "kernel_active": True,
        }

    rev_hash = str(nb.get("active_revision") or "prompt_signal")
    revisions = nb.get("revisions")
    if not isinstance(revisions, dict):
        revisions = {}
    rev = revisions.get(rev_hash)
    if not isinstance(rev, dict):
        rev = {"cells": {}, "last_seen_at": now_iso}
    rev_cells = rev.get("cells")
    if not isinstance(rev_cells, dict):
        rev_cells = {}

    cell_key = str(cell_index)
    prev_row = rev_cells.get(cell_key) if isinstance(rev_cells.get(cell_key), dict) else {}
    order_val = target.get("execution_order")
    try:
        order_int = int(order_val) if order_val is not None else None
    except (TypeError, ValueError):
        order_int = None

    seen_running = phase in ("queued", "running", "executed")
    rev_cells[cell_key] = {
        "baseline_order": order_int if order_int is not None else prev_row.get("baseline_order"),
        "seen_running": seen_running or bool(prev_row.get("seen_running")),
        "title": str(target.get("execution_title") or ""),
    }
    rev["cells"] = rev_cells
    rev["last_seen_at"] = now_iso
    revisions[rev_hash] = rev
    nb["revisions"] = revisions
    nb["active_revision"] = rev_hash
    nb["last_seen_at"] = now_iso
    state[tab_url] = nb
    _save_execution_state(state)

    order_log = order_int if order_int is not None else "?"
    emit(f"EXEC DETECTED cell={cell_index} order={order_log} phase={phase}")
    return True
