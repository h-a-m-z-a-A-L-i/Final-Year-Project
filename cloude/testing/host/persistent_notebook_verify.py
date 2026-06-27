"""Persistent JSON snapshot loading + fast terminal reporting for structure verification."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

PERSISTENT_VERIFY_POLL_SEC = float(os.environ.get("PERSISTENT_VERIFY_POLL_SEC", "0.03"))


def _debug_log(location: str, message: str, data: dict, hypothesis_id: str = "H1") -> None:
    # #region agent log
    try:
        repo = Path(__file__).resolve().parents[2]
        log_path = repo / "debug-f9d51b.log"
        payload = {
            "sessionId": "f9d51b",
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
            "hypothesisId": hypothesis_id,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


def report_verify_event(event: str, **fields: Any) -> None:
    """Emit verification progress to terminal immediately (stdout, flushed)."""
    payload = {"verify_event": event, "ts": time.time(), **fields}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    _debug_log("persistent_notebook_verify.py:report_verify_event", event, payload)


def normalize_cell_source(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").strip()


def source_hash(text: str) -> str:
    norm = normalize_cell_source(text)
    return hashlib.sha256(norm.encode("utf-8", errors="replace")).hexdigest()[:16]


def notebook_structure_hash(data: dict | None) -> str:
    """Fingerprint whole notebook cell list for persistent JSON change detection."""
    if not isinstance(data, dict):
        return ""
    cells = data.get("cells") or []
    parts: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        idx = cell.get("index")
        ctype = str(cell.get("type") or "code")
        src = normalize_cell_source(str(cell.get("input") or cell.get("source") or ""))
        parts.append(f"{idx}:{ctype}:{source_hash(src)}")
    blob = "|".join(parts)
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:16]


def persistent_snapshot_path(url: str) -> Path | None:
    try:
        from .notebook_storage import notebook_paths, resolve_storage_key
    except Exception:
        from notebook_storage import notebook_paths, resolve_storage_key  # type: ignore

    if not url:
        return None
    storage_key = resolve_storage_key(url)
    paths = notebook_paths(storage_key)
    path = paths["persistent"]
    if path.is_file():
        return path
    try:
        from .persistence_helpers import get_safe_filename, _current_scraped_dir
    except Exception:
        from persistence_helpers import get_safe_filename, _current_scraped_dir  # type: ignore
    legacy = _current_scraped_dir() / "persistent" / get_safe_filename(url)
    return legacy if legacy.is_file() else path


def load_persistent_notebook_snapshot(url: str) -> tuple[dict | None, str]:
    """Load notebook JSON from persistent store only (not live, not DOM)."""
    try:
        from .notebook_storage import load_notebook_file
    except Exception:
        from notebook_storage import load_notebook_file  # type: ignore

    path = persistent_snapshot_path(url)
    if path is None or not path.is_file():
        return None, "persistent"
    data = load_notebook_file(path)
    if isinstance(data, dict) and url:
        data = dict(data)
        data["tabUrl"] = url
    return data, "persistent"


def poll_persistent_snapshot(
    url: str,
    *,
    timeout: float,
    on_tick: Callable[[dict | None, str, float], dict[str, Any] | None],
    poll_sec: float | None = None,
) -> dict[str, Any]:
    """
    Poll persistent JSON until on_tick returns a success dict or timeout.
    on_tick(data, structure_hash, mtime) -> result dict with ok=True, or None to continue.
    """
    interval = max(0.01, float(poll_sec or PERSISTENT_VERIFY_POLL_SEC))
    deadline = time.monotonic() + max(0.3, float(timeout))
    path = persistent_snapshot_path(url)
    last_mtime = -1.0
    last_hash = ""
    last_data: dict | None = None

    report_verify_event("poll_start", url=url, path=str(path) if path else None, timeout=timeout)

    while time.monotonic() < deadline:
        mtime = 0.0
        if path and path.is_file():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0

        if mtime != last_mtime or last_data is None:
            data, _source = load_persistent_notebook_snapshot(url)
            struct_hash = notebook_structure_hash(data)
            if struct_hash != last_hash:
                report_verify_event(
                    "persistent_changed",
                    url=url,
                    structure_hash=struct_hash,
                    cell_count=len((data or {}).get("cells") or []) if isinstance(data, dict) else 0,
                    mtime=mtime,
                )
                last_hash = struct_hash
            last_mtime = mtime
            last_data = data
            hit = on_tick(data, struct_hash, mtime)
            if isinstance(hit, dict) and hit.get("ok"):
                report_verify_event("verified", url=url, wait_reason=hit.get("wait_reason"), **{
                    k: v for k, v in hit.items() if k not in ("ok",)
                })
                return hit

        time.sleep(interval)

    return {
        "ok": False,
        "error": f"persistent snapshot unchanged or condition not met within {timeout}s",
        "last_structure_hash": last_hash,
        "cell_count": len((last_data or {}).get("cells") or []) if isinstance(last_data, dict) else 0,
    }
