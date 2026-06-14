#!/usr/bin/env python3
"""Generate execution_integrity_report.json comparing before/after gate behavior."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_DIR))

from execution_integrity import (
    ExecutionIntegrityState,
    apply_final_integrity_gate,
    block_success_language_legacy_only,
    claims_success,
    update_integrity_from_verification,
)
from agent_goal_verification import apply_goal_verification_layer, sanitize_false_success_language

OUT = HOST_DIR / "data" / "logs" / "execution_integrity_report.json"
INTEGRITY_LOG = HOST_DIR / "data" / "logs" / "execution_integrity.jsonl"

SUCCESS_TEXT = (
    "The error in cell 30 has been fixed and runs successfully without errors."
)


def _cell30_verification():
    out = "KeyError: 'price'"
    base = {
        "verified": True,
        "tool_queue_complete": True,
        "batch_executed": True,
        "executed": [
            {"tool": "edit_cell_by_index", "cell_index": 30, "dispatched": True},
            {"tool": "run_cell", "cell_index": 30, "dispatched": True},
        ],
    }
    return apply_goal_verification_layer(
        base,
        user_prompt="fix the error in cell 30 and test until it runs successfully without errors",
        run_waits=[{"ok": True, "output": out, "run_succeeded": False}],
        run_indices=[30],
    )


def _scenario_results() -> list[dict]:
    scenarios = []

    v30 = _cell30_verification()
    state30 = update_integrity_from_verification(
        ExecutionIntegrityState(),
        parsed_tool_count=2,
        verification=v30,
        executor_called=True,
    )
    before30 = sanitize_false_success_language(SUCCESS_TEXT, v30)
    after30, blocked30 = apply_final_integrity_gate(
        SUCCESS_TEXT, state30, verification=v30, action_required=True
    )
    scenarios.append(
        {
            "id": "cell30_historical",
            "before_allowed_success": claims_success(before30),
            "after_allowed_success": claims_success(after30),
            "blocked": blocked30,
        }
    )

    state_b = ExecutionIntegrityState()
    state_b.parsed_tool_count = 2
    before_b = block_success_language_legacy_only(SUCCESS_TEXT, None)
    after_b, blocked_b = apply_final_integrity_gate(
        SUCCESS_TEXT, state_b, verification=None, action_required=True
    )
    scenarios.append(
        {
            "id": "parsed_no_executor",
            "before_allowed_success": claims_success(before_b),
            "after_allowed_success": claims_success(after_b),
            "blocked": blocked_b,
        }
    )

    state_c = ExecutionIntegrityState()
    state_c.executor_called = True
    before_c = block_success_language_legacy_only(SUCCESS_TEXT, None)
    after_c, blocked_c = apply_final_integrity_gate(
        SUCCESS_TEXT, state_c, verification=None, action_required=True
    )
    scenarios.append(
        {
            "id": "verification_missing",
            "before_allowed_success": claims_success(before_c),
            "after_allowed_success": claims_success(after_c),
            "blocked": blocked_c,
        }
    )

    v_ok = {
        "verified": True,
        "goal_verified": True,
        "batch_executed": True,
        "executed": [{"tool": "run_cell", "dispatched": True}],
        "tool_verifications": [{"tool": "run_cell", "verification_status": "verified"}],
    }
    state_ok = update_integrity_from_verification(
        ExecutionIntegrityState(), parsed_tool_count=1, verification=v_ok, executor_called=True
    )
    ok_text = "Cell 5 runs successfully without errors."
    _, blocked_ok = apply_final_integrity_gate(
        ok_text, state_ok, verification=v_ok, action_required=True
    )
    scenarios.append(
        {
            "id": "verified_success",
            "before_allowed_success": True,
            "after_allowed_success": True,
            "blocked": blocked_ok,
        }
    )

    return scenarios


def main() -> int:
    scenarios = _scenario_results()
    before_fp = sum(1 for s in scenarios if s["before_allowed_success"])
    after_fp = sum(1 for s in scenarios if s["after_allowed_success"])
    blocked = sum(1 for s in scenarios if s["blocked"])

    live_log = []
    if INTEGRITY_LOG.exists():
        for line in INTEGRITY_LOG.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    live_log.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "false_success_rate_before": round(before_fp / len(scenarios), 4),
            "false_success_rate_after": round(after_fp / len(scenarios), 4),
            "blocked_false_successes": blocked,
            "scenario_count": len(scenarios),
        },
        "remaining_failure_paths": [
            {
                "path": "direct_edit_bypass",
                "location": "streaming.py:~1145 direct_edit_done",
                "note": "Direct edit path may still claim update before integrity gate",
            },
            {
                "path": "mid_react_prose_break",
                "location": "streaming.py:~1644",
                "note": "Prose-only break before final summary — gate applies at turn end only",
            },
            {
                "path": "empty_run_output_verified",
                "location": "agent_goal_verification.verify_run_cell",
                "note": "Empty output may still pass tool verification if dispatch ok",
            },
        ],
        "scenarios": scenarios,
        "live_integrity_log_entries": len(live_log),
        "live_blocked_count": sum(1 for e in live_log if e.get("success_blocked")),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(json.dumps(report["metrics"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
