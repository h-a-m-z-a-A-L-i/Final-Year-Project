"""Verify edit_cell via dispatch + notebook snapshot / DOM content read (not extension ack)."""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from typing import Any

EDIT_VERIFY_TIMEOUT_SEC = float(os.environ.get("EDIT_CELL_VERIFY_TIMEOUT_SEC", "15"))
EDIT_VERIFY_POLL_SEC = float(os.environ.get("EDIT_CELL_VERIFY_POLL_SEC", "0.05"))
EDIT_DISPATCH_TIMEOUT_SEC = float(os.environ.get("EDIT_CELL_DISPATCH_TIMEOUT_SEC", "2.5"))
EDIT_CONTENT_POLL_MS = int(os.environ.get("EDIT_CELL_CONTENT_POLL_MS", "600"))


def _normalize_cell_source(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").strip()


def _source_hash(text: str) -> str:
    norm = _normalize_cell_source(text)
    return hashlib.sha256(norm.encode("utf-8", errors="replace")).hexdigest()[:16]


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


def capture_edit_baseline(url: str, cell_index: int) -> dict[str, Any]:
    try:
        from .notebook_context import load_notebook_snapshot
    except Exception:
        from notebook_context import load_notebook_snapshot  # type: ignore

    data, source = load_notebook_snapshot(url)
    before_cell = _cell_by_index(data if isinstance(data, dict) else None, cell_index)
    before_input = str((before_cell or {}).get("input") or (before_cell or {}).get("source") or "")
    return {
        "url": url,
        "cell_index": int(cell_index),
        "snapshot": data if isinstance(data, dict) else {},
        "snapshot_source": source,
        "before_cell": before_cell,
        "before_input": before_input,
        "before_hash": _source_hash(before_input),
    }


def dispatch_edit_cell(cmd: dict, *, timeout: float = EDIT_DISPATCH_TIMEOUT_SEC) -> dict[str, Any]:
    """Fire set_cell_content in browser; do not wait for frame/extension ack."""
    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command  # type: ignore

    attempt = dict(cmd)
    attempt["action"] = "set_cell_content"
    attempt["index_basis"] = "dom"
    attempt["requestId"] = str(attempt.get("requestId") or uuid.uuid4())
    attempt["fire_and_forget"] = True
    attempt["wait_for_result"] = False
    attempt["timeout"] = min(float(timeout), float(attempt.get("timeout") or timeout))
    if attempt.get("dom_index") is not None and attempt.get("cellIndex") is None:
        attempt["cellIndex"] = attempt["dom_index"]
    return execute_bot_command(attempt, timeout=float(timeout))


def _read_cell_content_browser(url: str, dom_index: int, tab_id: int | None) -> str:
    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command  # type: ignore

    cmd: dict[str, Any] = {
        "action": "get_cell_content",
        "url": url,
        "cell_index": int(dom_index) + 1,
        "cellIndex": int(dom_index),
        "dom_index": int(dom_index),
        "index_basis": "dom",
        "requestId": str(uuid.uuid4()),
        "timeout": max(2.5, EDIT_CONTENT_POLL_MS / 1000.0 + 1.5),
        "maxWaitMs": EDIT_CONTENT_POLL_MS,
    }
    if isinstance(tab_id, int) and tab_id > 0:
        cmd["tab_id"] = tab_id
    elif url:
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
    payload = inner.get("result") if isinstance(inner.get("result"), dict) else inner
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("content") or "")


def wait_for_edit_verification(
    url: str,
    cell_index: int,
    expected_content: str,
    *,
    before_input: str = "",
    before_hash: str = "",
    dom_index: int | None = None,
    tab_id: int | None = None,
    timeout: float = EDIT_VERIFY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Confirm cell source matches expected content in snapshot or live DOM."""
    try:
        from .notebook_context import load_notebook_snapshot
    except Exception:
        from notebook_context import load_notebook_snapshot  # type: ignore

    idx = int(cell_index)
    want = _normalize_cell_source(expected_content)
    before_norm = _normalize_cell_source(before_input)
    before_h = before_hash or _source_hash(before_input)
    dom_idx = int(dom_index) if dom_index is not None else idx - 1

    if not want:
        return {
            "ok": False,
            "edit_verified": False,
            "error": "content is required for edit verification",
            "cell_index": idx,
        }

    deadline = time.monotonic() + max(0.5, float(timeout))
    browser_next = time.monotonic()
    last_current = ""

    def _matches(current: str) -> bool:
        cur_norm = _normalize_cell_source(current)
        if cur_norm != want:
            return False
        cur_h = _source_hash(current)
        if cur_h == before_h and before_norm == want:
            return False
        if before_norm and cur_norm == before_norm:
            return False
        return True

    while time.monotonic() < deadline:
        data, _source = load_notebook_snapshot(url)
        row = _cell_by_index(data, idx)
        if row:
            current = str(row.get("input") or row.get("source") or "")
            last_current = current or last_current
            if _matches(current):
                return {
                    "ok": True,
                    "edit_verified": True,
                    "wait_reason": "snapshot_input",
                    "cell_index": idx,
                    "dom_index": dom_idx,
                    "chars": len(want),
                    "phase": "content_set",
                }

        now = time.monotonic()
        if now >= browser_next:
            browser_next = now + max(0.5, EDIT_CONTENT_POLL_MS / 1000.0)
            dom_content = _read_cell_content_browser(url, dom_idx, tab_id)
            if dom_content:
                last_current = dom_content
                if _matches(dom_content):
                    return {
                        "ok": True,
                        "edit_verified": True,
                        "wait_reason": "dom_cell_content",
                        "cell_index": idx,
                        "dom_index": dom_idx,
                        "chars": len(want),
                        "phase": "content_set",
                    }

        time.sleep(max(0.02, EDIT_VERIFY_POLL_SEC))

    preview = _normalize_cell_source(last_current)[:120]
    return {
        "ok": False,
        "edit_verified": False,
        "error": (
            f"edit not verified for cell {idx} — expected {len(want)} chars, "
            f"last seen {len(_normalize_cell_source(last_current))} chars"
            + (f" (preview: {preview!r})" if preview else "")
        ),
        "cell_index": idx,
        "dom_index": dom_idx,
        "expected_chars": len(want),
        "last_chars": len(_normalize_cell_source(last_current)),
    }
