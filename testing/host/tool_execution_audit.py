"""End-to-end tool execution pipeline audit logging (read-only instrumentation)."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT
except Exception:
    from config import DATA_ROOT

AUDIT_LOG = DATA_ROOT / "logs" / "tool_execution_audit.jsonl"
_LOCK = threading.Lock()

# Hook locations in streaming.py (for report line references)
STREAMING_HOOKS = {
    "parse": "streaming.py:~1301 parse_text_tool_batch_result",
    "plan_skip": "streaming.py:~1426 plan_generation_pending continue (tools skipped)",
    "prose_only_break": "streaming.py:~1438-1644 no tool_calls path",
    "batch_dispatch": "streaming.py:~1673 should_use_batch_executor",
    "batch_execute": "streaming.py:~1679 execute_agentic_batch",
    "workflow_continue": "streaming.py:~1845 workflow_needs_llm_followup",
    "workflow_stop": "streaming.py:~1870 react_stop break",
    "sequential_execute": "streaming.py:~1880 per-tool reg.call loop",
    "final_response": "streaming.py:~2096 sanitize_false_success_language",
    "instruction_guard": "streaming.py:~2050 final_instruction_only_guard",
}

EXECUTOR_HOOK = "agentic_batch_executor.py:~1418 registry.call per tool"

_SUCCESS_CLAIM_RE = re.compile(
    r"\b(fixed|successfully|verified|resolved|completed|no error|working now|without errors)\b",
    re.I,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_names_from_calls(tool_calls: list[dict] | None) -> list[str]:
    names: list[str] = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _assistant_claims_success(text: str) -> bool:
    return bool(_SUCCESS_CLAIM_RE.search(str(text or "")))


def build_tool_record(
    *,
    session_id: str | None,
    round_index: int,
    tool_name: str,
    parsed_success: bool = False,
    dispatched_success: bool | None = None,
    executor_called: bool = False,
    executor_result: dict[str, Any] | None = None,
    verification_received: bool = False,
    verification_success: bool | None = None,
    final_response_claimed_success: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "ts": _now(),
        "record_type": "tool",
        "session_id": session_id,
        "round_index": int(round_index),
        "tool_name": tool_name,
        "parsed_success": bool(parsed_success),
        "dispatched_success": dispatched_success,
        "executor_called": bool(executor_called),
        "executor_result": executor_result,
        "verification_received": bool(verification_received),
        "verification_success": verification_success,
        "final_response_claimed_success": bool(final_response_claimed_success),
    }
    if extra:
        rec.update(extra)
    return rec


def build_batch_record(
    *,
    session_id: str | None,
    round_index: int,
    goal: str = "",
    notebook_url: str | None = None,
    parsed_tools: list[str] | None = None,
    parsed_tool_count: int = 0,
    parse_recovery_used: bool = False,
    dispatch_path: str | None = None,
    dispatcher_received: bool = False,
    executor_called: bool = False,
    executor_returned: bool = False,
    executor_ok: bool | None = None,
    verification_received: bool = False,
    verification_success: bool | None = None,
    verification_summary: dict[str, Any] | None = None,
    workflow_continue: bool | None = None,
    workflow_stop_reason: str | None = None,
    assistant_final_text: str | None = None,
    assistant_claimed_success: bool = False,
    tool_records: list[dict[str, Any]] | None = None,
    pipeline_stop_stage: str | None = None,
    failure_case: str | None = None,
    source_hooks: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "ts": _now(),
        "record_type": "batch",
        "session_id": session_id,
        "round": int(round_index),
        "goal": str(goal or "").strip(),
        "notebook_url": notebook_url,
        "parsed_tools": list(parsed_tools or []),
        "parsed_tool_count": int(parsed_tool_count),
        "parse_recovery_used": bool(parse_recovery_used),
        "dispatch_path": dispatch_path,
        "dispatcher_received": bool(dispatcher_received),
        "executor_called": bool(executor_called),
        "executor_returned": bool(executor_returned),
        "executor_ok": executor_ok,
        "verification_received": bool(verification_received),
        "verification_success": verification_success,
        "verification_summary": verification_summary,
        "workflow_continue": workflow_continue,
        "workflow_stop_reason": workflow_stop_reason,
        "assistant_claimed_success": bool(assistant_claimed_success),
        "assistant_final_text_preview": (assistant_final_text or "")[:500],
        "tool_records": list(tool_records or []),
        "pipeline_stop_stage": pipeline_stop_stage,
        "failure_case": failure_case,
        "source_hooks": list(source_hooks or []),
    }
    if extra:
        rec.update(extra)
    return rec


def classify_failure_case(batch: dict[str, Any]) -> str | None:
    """Detect CASE A–F from a batch audit record."""
    parsed = int(batch.get("parsed_tool_count") or 0)
    dispatched = bool(batch.get("dispatcher_received"))
    executor_called = bool(batch.get("executor_called"))
    executor_ok = batch.get("executor_ok")
    ver_recv = bool(batch.get("verification_received"))
    ver_ok = batch.get("verification_success")
    claimed = bool(batch.get("assistant_claimed_success"))

    if parsed > 0 and not dispatched:
        return "CASE_A"
    if dispatched and not executor_called:
        return "CASE_B"
    if executor_called and executor_ok is False:
        return "CASE_C"
    if executor_called and executor_ok is True and not ver_recv:
        return "CASE_D"
    if ver_recv and ver_ok is False and claimed:
        return "CASE_E"
    if not ver_recv and claimed and parsed > 0:
        return "CASE_F"
    return None


def infer_pipeline_stop_stage(batch: dict[str, Any]) -> str:
    case = batch.get("failure_case") or classify_failure_case(batch)
    mapping = {
        "CASE_A": "after_parse_before_dispatch",
        "CASE_B": "dispatch_without_executor",
        "CASE_C": "executor_error",
        "CASE_D": "executor_ok_verification_missing",
        "CASE_E": "verification_failed_success_claimed",
        "CASE_F": "verification_missing_success_assumed",
    }
    if case in mapping:
        return mapping[case]
    if int(batch.get("parsed_tool_count") or 0) == 0:
        return "parse_zero_tools"
    if batch.get("verification_success") is True:
        return "pipeline_complete"
    if batch.get("executor_called") and batch.get("verification_received"):
        return "verification_failed_continue_or_stop"
    return "unknown"


def tool_records_from_verification(
    *,
    session_id: str | None,
    round_index: int,
    parsed_tools: list[str],
    verification: dict[str, Any] | None,
    assistant_claimed_success: bool = False,
) -> list[dict[str, Any]]:
    executed = list((verification or {}).get("executed") or [])
    ver_ok = (verification or {}).get("verified")
    ver_recv = verification is not None and bool(verification)
    by_name: dict[str, dict] = {}
    for ex in executed:
        if isinstance(ex, dict):
            by_name[str(ex.get("tool") or "")] = ex

    records: list[dict[str, Any]] = []
    for name in parsed_tools:
        ex = by_name.get(name) or {}
        records.append(
            build_tool_record(
                session_id=session_id,
                round_index=round_index,
                tool_name=name,
                parsed_success=True,
                dispatched_success=bool(ex.get("dispatched")) if ex else None,
                executor_called=bool(ex),
                executor_result=ex if ex else None,
                verification_received=ver_recv,
                verification_success=bool(ver_ok) if ver_recv else None,
                final_response_claimed_success=assistant_claimed_success,
            )
        )
    return records


def append_audit_record(record: dict[str, Any]) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _LOCK:
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def log_batch_audit(record: dict[str, Any]) -> dict[str, Any]:
    if not record.get("failure_case"):
        record["failure_case"] = classify_failure_case(record)
    if not record.get("pipeline_stop_stage"):
        record["pipeline_stop_stage"] = infer_pipeline_stop_stage(record)
    append_audit_record(record)
    return record


def summarize_verification(verification: dict[str, Any] | None) -> dict[str, Any]:
    v = verification or {}
    exec_err = v.get("execution_error") or {}
    return {
        "verified": v.get("verified"),
        "goal_verified": v.get("goal_verified"),
        "goal_reason": v.get("goal_reason"),
        "batch_executed": v.get("batch_executed"),
        "tool_queue_complete": v.get("tool_queue_complete"),
        "run_queue_complete": v.get("run_queue_complete"),
        "needs_fix": v.get("needs_fix"),
        "execution_error": exec_err if exec_err else None,
        "executed_count": len(v.get("executed") or []),
        "pending_run_cells": v.get("pending_run_cells"),
    }
