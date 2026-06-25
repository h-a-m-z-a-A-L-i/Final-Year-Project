"""Verify select_cell via dispatch + active-cell DOM read (not extension dispatch ack)."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

SELECT_VERIFY_TIMEOUT_SEC = float(os.environ.get("SELECT_CELL_VERIFY_TIMEOUT_SEC", "8"))
SELECT_VERIFY_POLL_SEC = float(os.environ.get("SELECT_CELL_VERIFY_POLL_SEC", "0.05"))
SELECT_DISPATCH_TIMEOUT_SEC = float(os.environ.get("SELECT_CELL_DISPATCH_TIMEOUT_SEC", "2.5"))
SELECT_ACTIVE_POLL_MS = int(os.environ.get("SELECT_CELL_ACTIVE_POLL_MS", "400"))


def dispatch_select_cell(cmd: dict, *, timeout: float = SELECT_DISPATCH_TIMEOUT_SEC) -> dict[str, Any]:
    """Fire select in browser; do not wait for frame/extension ack."""
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


def _read_active_cell_browser(url: str, tab_id: int | None) -> dict[str, Any]:
    try:
        from .bot_command import execute_bot_command
    except Exception:
        from bot_command import execute_bot_command  # type: ignore

    cmd: dict[str, Any] = {
        "action": "get_active_cell_index",
        "url": url,
        "requestId": str(uuid.uuid4()),
        "timeout": max(2.0, SELECT_ACTIVE_POLL_MS / 1000.0 + 1.0),
        "maxWaitMs": SELECT_ACTIVE_POLL_MS,
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
        return {}

    if not event.get("ok"):
        return {}

    inner = event.get("result") if isinstance(event.get("result"), dict) else {}
    payload = inner.get("result") if isinstance(inner.get("result"), dict) else inner
    if not isinstance(payload, dict):
        return {}
    return payload


def wait_for_select_verification(
    url: str,
    *,
    dom_index: int,
    app_index: int,
    tab_id: int | None = None,
    timeout: float = SELECT_VERIFY_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Confirm the requested cell is active/selected in the notebook DOM."""
    deadline = time.monotonic() + max(0.3, float(timeout))
    want_dom = int(dom_index)
    want_app = int(app_index)
    last_active: dict[str, Any] = {}
    browser_next = time.monotonic()

    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= browser_next:
            browser_next = now + max(0.4, SELECT_ACTIVE_POLL_MS / 1000.0)
            active = _read_active_cell_browser(url, tab_id)
        else:
            active = None

        if active:
            last_active = active
            dom = active.get("domIndex")
            app = active.get("appIndex")
            try:
                if dom is not None and int(dom) == want_dom:
                    return {
                        "ok": True,
                        "select_verified": True,
                        "wait_reason": "active_cell_dom",
                        "dom_index": want_dom,
                        "app_index": want_app,
                        "strategy": active.get("strategy"),
                        "data_windowed_list_index": active.get("dataWindowedListIndex"),
                        "phase": "selected",
                    }
                if app is not None and int(app) == want_app:
                    return {
                        "ok": True,
                        "select_verified": True,
                        "wait_reason": "active_cell_app",
                        "dom_index": want_dom,
                        "app_index": want_app,
                        "strategy": active.get("strategy"),
                        "data_windowed_list_index": active.get("dataWindowedListIndex"),
                        "phase": "selected",
                    }
            except (TypeError, ValueError):
                pass
        time.sleep(max(0.02, SELECT_VERIFY_POLL_SEC))

    err = "select not verified — is host.py running and the notebook tab open?"
    if last_active:
        err = (
            f"select not verified for cell {want_app} "
            f"(active dom={last_active.get('domIndex')} app={last_active.get('appIndex')})"
        )
    return {
        "ok": False,
        "select_verified": False,
        "error": err,
        "dom_index": want_dom,
        "app_index": want_app,
        "last_active": last_active or None,
    }
