"""Persist and discover live Chrome notebook tab + url for browser tool tests."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import BOT_RESULTS_PATH, DATA_ROOT, LOG_PATH
except Exception:
    from config import BOT_RESULTS_PATH, DATA_ROOT, LOG_PATH  # type: ignore

LAST_BROWSER_TARGETS_PATH = DATA_ROOT / "meta" / "last_browser_targets.json"
_MIN_VALID_TAB_ID = 50_000
_HOST_LOG_TAB_RE = re.compile(r"\[TAB (\d+)\]")
_HOST_LOG_IDENTITY_RE = re.compile(
    r"\[notebook_identity\]\s+(https://www\.kaggle\.com/code/[^\s]+/edit)"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_store() -> dict[str, Any]:
    if not LAST_BROWSER_TARGETS_PATH.is_file():
        return {"tabs": [], "last": None}
    try:
        data = json.loads(LAST_BROWSER_TARGETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"tabs": [], "last": None}
    if not isinstance(data, dict):
        return {"tabs": [], "last": None}
    data.setdefault("tabs", [])
    return data


def _write_store(data: dict[str, Any]) -> None:
    LAST_BROWSER_TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_BROWSER_TARGETS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def record_browser_target(tab_id: int | None, url: str | None) -> None:
    """Called from host when NOTEBOOK_DATA arrives for an /edit tab."""
    if not isinstance(tab_id, int) or tab_id < _MIN_VALID_TAB_ID:
        return
    url = str(url or "").strip()
    if not url or "/edit" not in url:
        return

    ts = _utc_now()
    entry = {"tabId": tab_id, "url": url, "ts": ts}
    data = _read_store()
    tabs: list[dict[str, Any]] = [
        t for t in data.get("tabs") or [] if isinstance(t, dict) and t.get("tabId") != tab_id
    ]
    tabs.insert(0, entry)
    data["tabs"] = tabs[:20]
    data["last"] = entry
    _write_store(data)


def _scan_bot_results_tail(max_lines: int = 400) -> list[dict[str, Any]]:
    if not BOT_RESULTS_PATH.is_file():
        return []
    try:
        lines = BOT_RESULTS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    seen: set[tuple[int, str]] = set()
    out: list[dict[str, Any]] = []
    for line in reversed(lines[-max_lines:]):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        tab_id = row.get("tabId")
        url = str(row.get("url") or "").strip()
        if not isinstance(tab_id, int) or tab_id < _MIN_VALID_TAB_ID or not url:
            continue
        key = (tab_id, url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"tabId": tab_id, "url": url, "ts": row.get("ts"), "source": "bot_results"})
    return out


def _scan_host_log_tail(max_bytes: int = 256_000) -> list[dict[str, Any]]:
    if not LOG_PATH.is_file():
        return []
    try:
        raw = LOG_PATH.read_bytes()
    except Exception:
        return []
    chunk = raw[-max_bytes:].decode("utf-8", errors="replace")
    lines = chunk.splitlines()
    pending_url: str | None = None
    seen: set[tuple[int, str]] = set()
    out: list[dict[str, Any]] = []
    for line in lines:
        id_match = _HOST_LOG_IDENTITY_RE.search(line)
        if id_match:
            pending_url = id_match.group(1)
            continue
        tab_match = _HOST_LOG_TAB_RE.search(line)
        if tab_match and pending_url:
            tab_id = int(tab_match.group(1))
            if tab_id < _MIN_VALID_TAB_ID:
                continue
            key = (tab_id, pending_url)
            if key not in seen:
                seen.add(key)
                out.append(
                    {
                        "tabId": tab_id,
                        "url": pending_url,
                        "ts": None,
                        "source": "host.log",
                    }
                )
            pending_url = None
    return list(reversed(out))


def is_host_extension_live(max_age_sec: float = 120.0) -> bool:
    """True if host.log was recently updated (extension scraping) or shows fresh TAB lines."""
    if not LOG_PATH.is_file():
        return False
    try:
        age = datetime.now().timestamp() - LOG_PATH.stat().st_mtime
        if age <= max_age_sec:
            return True
    except Exception:
        pass
    try:
        tail = LOG_PATH.read_bytes()[-4096:].decode("utf-8", errors="replace")
    except Exception:
        return False
    return "[TAB " in tail or "NOTEBOOK_DATA" in tail


def discover_browser_tabs() -> list[dict[str, Any]]:
    """Merge persisted, host.log, and bot_results tab/url pairs (newest first)."""
    merged: dict[tuple[int, str], dict[str, Any]] = {}

    store = _read_store()
    for row in store.get("tabs") or []:
        if not isinstance(row, dict):
            continue
        tab_id = row.get("tabId")
        url = str(row.get("url") or "").strip()
        if isinstance(tab_id, int) and url:
            merged[(tab_id, url)] = {**row, "source": row.get("source") or "persisted"}

    for row in _scan_host_log_tail():
        key = (row["tabId"], row["url"])
        if key not in merged:
            merged[key] = row

    for row in _scan_bot_results_tail():
        key = (row["tabId"], row["url"])
        if key not in merged:
            merged[key] = row

    tabs = list(merged.values())
    last = store.get("last")
    if isinstance(last, dict) and last.get("tabId") and last.get("url"):
        last_key = (last["tabId"], last["url"])
        tabs.sort(
            key=lambda r: (
                0 if (r.get("tabId"), r.get("url")) == last_key else 1,
                str(r.get("ts") or ""),
            ),
            reverse=False,
        )
    return tabs


def last_browser_target() -> dict[str, Any] | None:
    store = _read_store()
    last = store.get("last")
    if isinstance(last, dict) and last.get("tabId") and last.get("url"):
        return last
    tabs = discover_browser_tabs()
    return tabs[0] if tabs else None


def resolve_tab_id_for_url(url: str) -> int | None:
    """Best-effort tab id for a notebook /edit URL from recent session data."""
    want = str(url or "").strip()
    if not want:
        return None
    try:
        from .bot_tool_utils import notebook_urls_match
    except Exception:
        from bot_tool_utils import notebook_urls_match  # type: ignore

    for row in discover_browser_tabs():
        row_url = str(row.get("url") or "").strip()
        if not row_url:
            continue
        if notebook_urls_match(want, row_url):
            tab_id = row.get("tabId")
            if isinstance(tab_id, int) and tab_id >= _MIN_VALID_TAB_ID:
                return tab_id
    return None


def auto_fill_browser_args(args: dict) -> tuple[dict, dict[str, Any] | None]:
    """
    If url/tab missing, fill from last known session.
    Returns (args, hint) where hint describes what was auto-filled.
    """
    out = dict(args)
    has_url = bool(str(out.get("url") or "").strip())
    tid = out.get("tab_id")
    has_tab = isinstance(tid, int) and tid >= _MIN_VALID_TAB_ID
    if has_url or has_tab:
        return out, None

    last = last_browser_target()
    if not last:
        return out, None

    out["tab_id"] = last["tabId"]
    out["url"] = last["url"]
    return out, {
        "auto_filled": True,
        "tab_id": last["tabId"],
        "url": last["url"],
        "hint": "Filled url+tab from last notebook session. Run: python testing/host/tools_testing/run.py tabs",
    }


def stale_tab_hint(tab_id: int | None) -> str | None:
    """Warn when tab id is not among recently seen notebook tabs."""
    if not isinstance(tab_id, int) or tab_id < _MIN_VALID_TAB_ID:
        return None
    known = {t["tabId"] for t in discover_browser_tabs()}
    if known and tab_id not in known:
        sample = ", ".join(str(t) for t in sorted(known)[:5])
        return (
            f"tab={tab_id} is not in recent notebook tabs ({sample}). "
            "Run: python testing/host/tools_testing/run.py tabs"
        )
    return None
