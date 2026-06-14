"""Compact batch verification payloads for ReAct tool rounds."""

from __future__ import annotations

import json
from typing import Any

try:
    from .agent_state import REACT_VERIFICATION
except Exception:
    from agent_state import REACT_VERIFICATION

VERIFICATION_MARKER = "__react_batch_verification__"


def build_compact_batch_verification(verification: dict[str, Any]) -> dict[str, Any]:
    """Shrink full verification dict for a single LLM context message."""
    pipeline = verification.get("pipeline") or {}
    tool_queue = verification.get("tool_queue") or {}
    err = verification.get("execution_error") or {}
    if not isinstance(err, dict):
        err = {"error_summary": str(err)} if err else {}
    evidence = verification.get("queue_cell_evidence") or {}
    cells = evidence.get("cells") if isinstance(evidence, dict) else None
    compact_cells = None
    if isinstance(cells, list):
        compact_cells = []
        for c in cells[:8]:
            if not isinstance(c, dict):
                continue
            compact_cells.append(
                {
                    "cell_index": c.get("cell_index"),
                    "input_preview": str(c.get("input") or "")[:200],
                    "output_preview": str(c.get("output") or "")[:300],
                }
            )
        if len(cells) > 8:
            compact_cells.append({"note": f"+{len(cells) - 8} more cells in snapshot tools"})

    return {
        "batch_status": verification.get("tool_queue_status")
        or ("verified" if verification.get("verified") else "pending"),
        "verified": verification.get("verified"),
        "batch_executed": verification.get("batch_executed"),
        "needs_fix": verification.get("needs_fix"),
        "cell_index": verification.get("cell_index"),
        "cell_output_preview": str(verification.get("cell_output") or "")[:400],
        "completed": tool_queue.get("run_completed") or verification.get("runs_executed") or [],
        "pending": verification.get("pending_run_cells") or tool_queue.get("run_pending") or [],
        "deferred_tool_calls": verification.get("deferred_tool_calls") or [],
        "pipeline_state": {
            "active": pipeline.get("active"),
            "complete": pipeline.get("complete"),
            "pending_runs": pipeline.get("pending_runs"),
            "completed_runs": pipeline.get("completed_runs"),
        },
        "execution_error": {
            "cell_index": err.get("cell_index"),
            "error_type": err.get("error_type"),
            "error_summary": str(err.get("error_summary") or "")[:400],
        }
        if err
        else None,
        "executed": [
            {"tool": x.get("tool"), "cell_index": x.get("cell_index")}
            for x in (verification.get("executed") or [])[:12]
            if isinstance(x, dict)
        ],
        "user_response_gate": str(verification.get("user_response_gate") or "")[:500] or None,
        "target_cells_preview": compact_cells,
        "await_llm_summary": verification.get("await_llm_summary"),
        "close_react_loop": verification.get("close_react_loop"),
        "unknown_tools": verification.get("unknown_tools") or [],
        "parse_feedback": verification.get("parse_feedback"),
        "goal_verified": verification.get("goal_verified"),
        "goal_reason": str(verification.get("goal_reason") or "")[:400] or None,
        "batch_audit": {
            k: verification.get("batch_audit", {}).get(k)
            for k in (
                "tools_requested", "tools_executed", "tools_verified", "tools_failed",
                "goal_verified", "failed_cells", "next_required_action",
            )
            if isinstance(verification.get("batch_audit"), dict)
        } or None,
    }


def append_batch_verification_message(
    tool_messages: list[dict[str, Any]],
    verification: dict[str, Any],
    *,
    round_idx: int,
    compact_fn=None,
) -> str:
    """
    Append exactly one verification message. Returns serialized payload (for tests).
    Uses role=user + marker for Cerebras text-tool compatibility and trim protection.
    """
    try:
        from .streaming import _compact_tool_result_content
    except Exception:
        try:
            from streaming import _compact_tool_result_content
        except Exception:
            def _compact_tool_result_content(text: str) -> str:
                return text

    compact = build_compact_batch_verification(verification)
    report = verification.get("execution_report")
    if report:
        compact["execution_report"] = report
    if verification.get("user_response_gate") and verification.get("strict_goal_verified") is False:
        compact["user_response_gate"] = str(verification.get("user_response_gate") or "")[:1500]
    if compact_fn:
        compact = compact_fn(verification)
    payload = _compact_tool_result_content(json.dumps(compact, ensure_ascii=False))
    tool_messages.append(
        {
            "role": "user",
            "content": f"{VERIFICATION_MARKER}\n{payload}",
            REACT_VERIFICATION: True,
        }
    )
    return payload


def count_verification_messages(messages: list[dict[str, Any]]) -> int:
    n = 0
    for m in messages or []:
        if m.get(REACT_VERIFICATION):
            n += 1
            continue
        content = str(m.get("content") or "")
        if VERIFICATION_MARKER in content:
            n += 1
    return n
