"""Agent turn metrics — persisted to data/logs/agent_metrics.json."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT
except Exception:
    from config import DATA_ROOT

METRICS_PATH = DATA_ROOT / "logs" / "agent_metrics.json"
_LOCK = threading.Lock()

_COUNTERS = (
    "turns_total",
    "tool_batch_parse_attempts",
    "tool_batch_parse_success",
    "tool_batch_execution_success",
    "tool_batch_repair_attempts",
    "tool_batch_repair_success",
    "unknown_tool_events",
    "prose_only_events",
    "prose_only_early_stops",
    "multiple_batch_merge_events",
    "tasks_completed",
    "tasks_failed",
    "plan_created",
    "step_completed",
    "step_failed",
    "step_retried",
)


def _empty_metrics() -> dict[str, Any]:
    base = {k: 0 for k in _COUNTERS}
    base["rates"] = {
        "tool_batch_success_rate": 0.0,
        "tool_batch_parse_rate": 0.0,
        "tool_batch_execution_rate": 0.0,
        "tool_batch_repair_rate": 0.0,
        "unknown_tool_rate": 0.0,
        "prose_only_rate": 0.0,
    }
    base["recent_turns"] = []
    base["updated_at"] = None
    return base


def _load() -> dict[str, Any]:
    if not METRICS_PATH.is_file():
        return _empty_metrics()
    try:
        data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            out = _empty_metrics()
            out.update(data)
            return out
    except Exception:
        pass
    return _empty_metrics()


def _save(data: dict[str, Any]) -> None:
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _recompute_rates(data)
    METRICS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _recompute_rates(data: dict[str, Any]) -> None:
    turns = max(1, int(data.get("turns_total") or 0))
    parse_attempts = max(1, int(data.get("tool_batch_parse_attempts") or 0))
    parse_ok = int(data.get("tool_batch_parse_success") or 0)
    exec_ok = int(data.get("tool_batch_execution_success") or 0)
    repair_attempts = max(1, int(data.get("tool_batch_repair_attempts") or 0))
    repair_ok = int(data.get("tool_batch_repair_success") or 0)
    data["rates"] = {
        "tool_batch_success_rate": round(parse_ok / parse_attempts, 4),
        "tool_batch_parse_rate": round(parse_ok / parse_attempts, 4),
        "tool_batch_execution_rate": round(exec_ok / max(1, parse_ok), 4),
        "tool_batch_repair_rate": round(repair_ok / repair_attempts, 4),
        "unknown_tool_rate": round(int(data.get("unknown_tool_events") or 0) / turns, 4),
        "prose_only_rate": round(int(data.get("prose_only_events") or 0) / turns, 4),
    }


def record_turn_metric(
    *,
    event: str,
    extra: dict[str, Any] | None = None,
    increment: dict[str, int] | None = None,
) -> dict[str, Any]:
    with _LOCK:
        data = _load()
        for key, delta in (increment or {}).items():
            if key in _COUNTERS:
                data[key] = int(data.get(key) or 0) + int(delta)
        recent = list(data.get("recent_turns") or [])
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        if extra:
            entry.update(extra)
        recent.append(entry)
        data["recent_turns"] = recent[-100:]
        _save(data)
        return dict(data)


def read_metrics() -> dict[str, Any]:
    with _LOCK:
        return _load()
