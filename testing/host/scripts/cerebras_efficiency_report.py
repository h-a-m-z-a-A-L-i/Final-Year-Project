#!/usr/bin/env python3
"""Cerebras efficiency estimate from metrics + config limits."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.agent_metrics import read_metrics
from testing.host.config import LLM_REACT_MAX_ROUNDS
from testing.host.llm_provider import cerebras_rate_limits


def main() -> int:
    limits = cerebras_rate_limits()
    metrics = read_metrics()
    recent = list(metrics.get("recent_turns") or [])

    avg_tokens = 4500
    avg_calls = 2.5
    token_samples = [r.get("prompt_tokens_est") for r in recent if r.get("prompt_tokens_est")]
    if token_samples:
        avg_tokens = sum(token_samples) / len(token_samples)

    rpm = limits.get("rpm", 5)
    tpm = limits.get("tpm", 1_000_000)
    tpd = limits.get("tpd", 1_000_000)

    tokens_per_task = avg_tokens * avg_calls
    tasks_per_minute_rpm = rpm / avg_calls
    tasks_per_minute_tpm = tpm / max(1, tokens_per_task)
    tasks_per_day = tpd / max(1, tokens_per_task)

    report = {
        "assumptions": {
            "avg_tokens_per_call": round(avg_tokens, 0),
            "avg_llm_calls_per_task": avg_calls,
            "LLM_REACT_MAX_ROUNDS": LLM_REACT_MAX_ROUNDS,
        },
        "limits": {"rpm": rpm, "tpm": tpm, "tpd": tpd},
        "per_task": {
            "estimated_tokens": round(tokens_per_task, 0),
            "estimated_wall_min_at_5rpm": round((avg_calls / rpm) * 60 + avg_calls * 12, 1),
        },
        "capacity": {
            "max_tasks_per_minute_by_rpm": round(tasks_per_minute_rpm, 2),
            "max_tasks_per_minute_by_tpm": round(tasks_per_minute_tpm, 2),
            "max_tasks_per_day_1M_tokens": round(tasks_per_day, 1),
        },
        "metrics_rates": metrics.get("rates"),
        "metrics_counters": {k: metrics.get(k) for k in (
            "turns_total", "prose_only_events", "prose_only_early_stops",
            "unknown_tool_events", "tool_batch_parse_success",
        )},
    }
    out = REPO / "testing/host/data/logs/cerebras_efficiency_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
