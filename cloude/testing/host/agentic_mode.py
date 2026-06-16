"""Agentic mode gate — browser tools, ReAct loop, and parallel tool calls.

Agentic features run when:
  1. Server allows it (`LLM_AGENTIC_ENABLED` in .env)
  2. Chat session mode is `agentic` (UI mode dropdown)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT, LLM_AGENTIC_ENABLED
except Exception:
    from config import DATA_ROOT, LLM_AGENTIC_ENABLED

AGENTIC_MODE_ID = "agentic"
_SETTINGS_PATH = DATA_ROOT / "meta" / "agentic_settings.json"
_SETTINGS_LOCK = threading.Lock()

_DEFAULT_SETTINGS: dict[str, Any] = {
    "dashboard_enabled": False,
}


def _read_settings() -> dict[str, Any]:
    with _SETTINGS_LOCK:
        if not _SETTINGS_PATH.is_file():
            return dict(_DEFAULT_SETTINGS)
        try:
            raw = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return dict(_DEFAULT_SETTINGS)
            out = dict(_DEFAULT_SETTINGS)
            out.update(raw)
            return out
        except Exception:
            return dict(_DEFAULT_SETTINGS)


def _write_settings(data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(_DEFAULT_SETTINGS)
    merged.update(data)
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _SETTINGS_LOCK:
        _SETTINGS_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def server_agentic_allowed() -> bool:
    return bool(LLM_AGENTIC_ENABLED)


def dashboard_agentic_enabled() -> bool:
    """Legacy setting file; always treated as enabled (mode dropdown is the user gate)."""
    return True


def set_dashboard_agentic_enabled(enabled: bool) -> dict[str, Any]:
    return _write_settings({"dashboard_enabled": bool(enabled)})


def get_agentic_settings() -> dict[str, Any]:
    return {
        "server_allowed": server_agentic_allowed(),
        "dashboard_enabled": dashboard_agentic_enabled(),
        "mode_id": AGENTIC_MODE_ID,
        "ready": server_agentic_allowed() and dashboard_agentic_enabled(),
    }


def is_agentic_chat_mode(mode: str | None) -> bool:
    return str(mode or "").strip().lower() == AGENTIC_MODE_ID


def agentic_session_active(
    mode: str | None,
    *,
    dashboard_enabled: bool | None = None,
) -> bool:
    """True when this chat turn may use browser tools and full ReAct behavior."""
    if not is_agentic_chat_mode(mode):
        return False
    return server_agentic_allowed()


def resolve_effective_chat_mode(ui_mode: str | None) -> tuple[str, str | None]:
    """Normalize UI mode; downgrade agentic if server disallows it."""
    mode = str(ui_mode or "ask").strip().lower()
    if mode == AGENTIC_MODE_ID and not server_agentic_allowed():
        return "code", "Agentic mode is disabled on the host (set LLM_AGENTIC_ENABLED=1)."
    return mode, None


def browser_tool_allowed(mode: str | None, tool_name: str) -> tuple[bool, str | None]:
    try:
        from .tool_registry import BROWSER_TOOL_NAMES
    except Exception:
        from tool_registry import BROWSER_TOOL_NAMES

    if tool_name not in BROWSER_TOOL_NAMES:
        return True, None
    if agentic_session_active(mode):
        return True, None
    return False, (
        f"Tool '{tool_name}' requires Agentic mode. "
        "Select Agentic in the chat mode dropdown."
    )
