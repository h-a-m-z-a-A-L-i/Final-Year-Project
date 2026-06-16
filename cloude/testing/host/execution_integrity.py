"""Host-side execution integrity gate — success claims require verified execution."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT
except Exception:
    from config import DATA_ROOT

INTEGRITY_LOG = DATA_ROOT / "logs" / "execution_integrity.jsonl"
_LOG_LOCK = threading.Lock()

_SUCCESS_CLAIM_RE = re.compile(
    r"\b("
    r"fixed|successfully|resolved|completed|no error|error has been fixed|"
    r"execution successful|task complete|all good|working now|without errors"
    r")\b",
    re.I,
)
_VERIFIED_SUCCESS_RE = re.compile(
    r"\b(verified|successfully verified|goal verified|task verified)\b",
    re.I,
)
_NEGATED_VERIFICATION_RE = re.compile(
    r"could not be verified|not be verified|cannot be verified|"
    r"execution could not|may not have been applied",
    re.I,
)


def claims_success(text: str) -> bool:
    raw = str(text or "")
    if _NEGATED_VERIFICATION_RE.search(raw):
        return False
    if _SUCCESS_CLAIM_RE.search(raw):
        return True
    return bool(_VERIFIED_SUCCESS_RE.search(raw))

INTEGRITY_BLOCK_MESSAGE = (
    "Execution could not be verified.\n"
    "Tool execution did not complete.\n"
    "The requested change may not have been applied."
)


@dataclass
class ExecutionIntegrityState:
    """Turn-level host gate. Defaults to not verified."""

    goal_verified: bool = False
    parsed_tool_count: int = 0
    executor_called: bool = False
    bot_commands_dispatched: int = 0
    verification_received: bool = False
    verification_success: bool = False
    block_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def count_dispatched_tools(verification: dict[str, Any] | None) -> int:
    executed = (verification or {}).get("executed") or []
    return sum(
        1
        for e in executed
        if isinstance(e, dict) and (e.get("dispatched") or e.get("ok"))
    )


def compute_host_goal_verified(
    state: ExecutionIntegrityState,
    verification: dict[str, Any] | None,
    *,
    action_required: bool,
) -> tuple[bool, str]:
    """
    Host-side goal_verified — may only become True when execution + verification prove success.
    Returns (goal_verified, reason_if_false).
    """
    if not action_required:
        return True, ""

    if state.parsed_tool_count > 0 and not state.executor_called:
        return False, "executor_never_ran"

    if state.parsed_tool_count > 0 and state.bot_commands_dispatched == 0:
        return False, "parsed_tools_no_bot_commands"

    if not state.verification_received or verification is None:
        if state.parsed_tool_count > 0 or state.executor_called:
            return False, "verification_missing"
        return False, "no_verified_execution"

    if verification.get("strict_goal_verified") is False:
        return False, str(verification.get("strict_goal_reason") or "strict_goal_verification_false")

    report = verification.get("execution_report") or {}
    run_results = report.get("results") or {}
    for _key, entry in run_results.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("run_verified") is False:
            return False, "run_not_verified"
        if entry.get("run_verified") and entry.get("success") is False:
            return False, "run_execution_failed"

    if verification.get("execution_error"):
        return False, "execution_error"

    tool_v = verification.get("tool_verifications") or []
    failed = [tv for tv in tool_v if tv.get("verification_status") == "failed"]
    if failed:
        return False, "tool_verification_failed"

    if not verification.get("goal_verified"):
        return False, "goal_verification_false"

    if "strict_goal_verified" in verification and not verification.get("strict_goal_verified"):
        return False, str(verification.get("strict_goal_reason") or "strict_goal_verification_false")

    if not verification.get("verified"):
        return False, "batch_not_verified"

    if state.parsed_tool_count > 0:
        requested = len(verification.get("executed") or []) or state.parsed_tool_count
        verified_count = sum(
            1 for tv in tool_v if tv.get("verification_status") == "verified"
        )
        if tool_v and verified_count < len(tool_v):
            return False, "multi_tool_not_all_verified"
        if state.bot_commands_dispatched < min(state.parsed_tool_count, requested):
            return False, "not_all_tools_dispatched"

    return True, ""


def update_integrity_from_verification(
    state: ExecutionIntegrityState,
    *,
    parsed_tool_count: int,
    verification: dict[str, Any] | None,
    executor_called: bool,
) -> ExecutionIntegrityState:
    state.parsed_tool_count = max(state.parsed_tool_count, int(parsed_tool_count))
    state.executor_called = state.executor_called or bool(executor_called)
    if verification is not None:
        state.verification_received = True
        state.bot_commands_dispatched = max(
            state.bot_commands_dispatched,
            count_dispatched_tools(verification),
        )
        state.verification_success = bool(
            verification.get("verified") and verification.get("goal_verified")
        )
    goal_ok, reason = compute_host_goal_verified(
        state,
        verification,
        action_required=True,
    )
    state.goal_verified = goal_ok
    if not goal_ok:
        state.block_reason = reason
    return state


def record_parsed_tools(state: ExecutionIntegrityState, count: int) -> ExecutionIntegrityState:
    state.parsed_tool_count = max(state.parsed_tool_count, int(count))
    if count > 0:
        state.goal_verified = False
    return state


def apply_final_integrity_gate(
    text: str,
    state: ExecutionIntegrityState,
    *,
    verification: dict[str, Any] | None,
    action_required: bool,
    goal: str = "",
    session_id: str | None = None,
    round_index: int = -2,
    llm_request_failed: bool = False,
) -> tuple[str, bool]:
    """
    Block success language unless host goal_verified is True.
    Returns (final_text, success_blocked).
    """
    raw = str(text or "")
    if llm_request_failed or raw.strip().startswith("LLM request failed:"):
        log_integrity_event(
            goal=goal,
            session_id=session_id,
            round_index=round_index,
            state=state,
            verification=verification,
            success_blocked=False,
            original_claimed_success=False,
            extra={"skipped": "llm_request_failed"},
        )
        return raw, False

    goal_ok, reason = compute_host_goal_verified(
        state,
        verification,
        action_required=action_required,
    )
    state.goal_verified = goal_ok
    if not goal_ok:
        state.block_reason = reason or state.block_reason

    original_claimed = claims_success(text)
    if goal_ok or not action_required:
        log_integrity_event(
            goal=goal,
            session_id=session_id,
            round_index=round_index,
            state=state,
            verification=verification,
            success_blocked=False,
            original_claimed_success=original_claimed,
        )
        return text, False

    if not original_claimed:
        log_integrity_event(
            goal=goal,
            session_id=session_id,
            round_index=round_index,
            state=state,
            verification=verification,
            success_blocked=False,
            original_claimed_success=False,
        )
        return text, False

    blocked_text = INTEGRITY_BLOCK_MESSAGE
    if verification and (verification.get("goal_reason") or verification.get("execution_error")):
        detail = verification.get("goal_reason") or (
            (verification.get("execution_error") or {}).get("error_summary")
        )
        if detail:
            blocked_text = f"{INTEGRITY_BLOCK_MESSAGE}\n\nDetails: {detail}"

    log_integrity_event(
        goal=goal,
        session_id=session_id,
        round_index=round_index,
        state=state,
        verification=verification,
        success_blocked=True,
        original_claimed_success=True,
        blocked_text_preview=blocked_text[:300],
    )
    return blocked_text, True


def log_integrity_event(
    *,
    goal: str,
    session_id: str | None,
    round_index: int,
    state: ExecutionIntegrityState,
    verification: dict[str, Any] | None,
    success_blocked: bool,
    original_claimed_success: bool = False,
    blocked_text_preview: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    record = {
        "ts": _now(),
        "goal": str(goal or "")[:500],
        "session_id": session_id,
        "round": int(round_index),
        "parsed_tools": state.parsed_tool_count,
        "executor_called": state.executor_called,
        "bot_commands_dispatched": state.bot_commands_dispatched,
        "verification_received": state.verification_received,
        "verification_success": state.verification_success,
        "goal_verified": state.goal_verified,
        "success_blocked": bool(success_blocked),
        "block_reason": state.block_reason,
        "original_claimed_success": original_claimed_success,
        "blocked_text_preview": blocked_text_preview,
        "verification_goal_verified": (
            verification.get("goal_verified") if verification else None
        ),
        "verification_verified": verification.get("verified") if verification else None,
    }
    if extra:
        record.update(extra)
    INTEGRITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _LOG_LOCK:
        with INTEGRITY_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def block_success_language_legacy_only(
    text: str,
    verification: dict[str, Any] | None,
) -> str:
    """Pre-gate behavior: only strip when verification dict says goal_verified is False."""
    if not text or not verification:
        return text
    if verification.get("goal_verified") is not False:
        return text
    if not claims_success(text):
        return text
    return INTEGRITY_BLOCK_MESSAGE
