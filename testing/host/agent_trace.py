"""ReAct observability — set AGENT_TRACE=1 to log to data/logs/agent_trace.log."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT
    from .context_budget import estimate_messages_tokens
except Exception:
    from config import DATA_ROOT
    from context_budget import estimate_messages_tokens

TRACE_PATH = DATA_ROOT / "logs" / "agent_trace.log"


def agent_trace_enabled() -> bool:
    return os.environ.get("AGENT_TRACE", "").strip().lower() in ("1", "true", "yes")


def _write(line: str) -> None:
    try:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRACE_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def trace_react_event(
    *,
    event: str,
    round_idx: int | None = None,
    messages: list[dict[str, Any]] | None = None,
    protected_indices: set[int] | None = None,
    removed_fingerprints: list[str] | None = None,
    agent_state: dict[str, Any] | None = None,
    verification_summary: dict[str, Any] | None = None,
    continue_reason: str = "",
    stop_reason: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    if not agent_trace_enabled():
        return
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry: dict[str, Any] = {
        "ts": ts,
        "event": event,
        "round": round_idx,
        "token_count": estimate_messages_tokens(messages) if messages else 0,
        "message_count": len(messages) if messages else 0,
    }
    if protected_indices is not None:
        entry["protected_indices"] = sorted(protected_indices)
    if removed_fingerprints:
        entry["removed_messages"] = removed_fingerprints
    if agent_state:
        entry["agent_state"] = {
            "goal_len": len(str(agent_state.get("goal") or "")),
            "completed": len(agent_state.get("completed_steps") or []),
            "pending": agent_state.get("pending_steps"),
            "has_error": bool(agent_state.get("last_error")),
            "plan_steps": len(agent_state.get("plan") or []),
            "current_step": agent_state.get("current_step"),
        }
    if verification_summary:
        entry["verification_summary"] = verification_summary
    if continue_reason:
        entry["continue_reason"] = continue_reason
    if stop_reason:
        entry["stop_reason"] = stop_reason
    if extra:
        entry.update(extra)
    _write(json.dumps(entry, ensure_ascii=False))
