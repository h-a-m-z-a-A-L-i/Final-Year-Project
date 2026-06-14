"""Diagnostics for action-required prompts where the model emits no tools."""

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

REFUSAL_LOG = DATA_ROOT / "logs" / "agent_tool_refusal.jsonl"
_REFUSAL_LOCK = threading.Lock()

_REFUSAL_PATTERNS = (
    r"\bi cannot\b",
    r"\bi can't\b",
    r"\bunable to (?:use|call|invoke)\b",
    r"\bdo not have access\b",
    r"\bcannot (?:directly|use tools)\b",
    r"\bnot able to (?:edit|run|execute)\b",
    r"\bwithout tool access\b",
)

_BATCH_TAG_RE = re.compile(r"<agent_tool_batch>", re.IGNORECASE)


def classify_tool_refusal_failure(
    *,
    raw_model_response: str,
    parse_result: Any | None = None,
) -> str:
    """
    Classify why parsed_tool_count == 0 for an action-required turn.

    Categories: TOOL_REFUSAL | PROSE_ONLY | MALFORMED_BATCH | EMPTY_BATCH | UNKNOWN_TOOL_ONLY
    """
    text = str(raw_model_response or "")
    low = text.lower()
    pr = parse_result
    unknown = list(getattr(pr, "unknown_tools", None) or [])
    parse_errors = list(getattr(pr, "parse_errors", None) or [])
    tool_calls = list(getattr(pr, "tool_calls", None) or [])
    batch_count = int(getattr(pr, "batch_count", 0) or 0)
    has_batch_tag = bool(_BATCH_TAG_RE.search(text))

    if any(re.search(p, low) for p in _REFUSAL_PATTERNS):
        return "TOOL_REFUSAL"
    if unknown and not tool_calls:
        return "UNKNOWN_TOOL_ONLY"
    if parse_errors and (has_batch_tag or batch_count > 0):
        return "MALFORMED_BATCH"
    if (has_batch_tag or batch_count > 0) and not parse_errors and not tool_calls:
        return "EMPTY_BATCH"
    if has_batch_tag and not tool_calls:
        return "MALFORMED_BATCH"
    return "PROSE_ONLY"


def _char_len(value: Any) -> int:
    return len(str(value or ""))


def measure_prompt_context_sizes(
    messages: list[dict[str, Any]] | None,
    *,
    agent_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase 4: block sizes for dilution analysis (read-only measurement)."""
    try:
        from .context_budget import estimate_tokens
    except Exception:
        from context_budget import estimate_tokens

    msgs = messages or []
    serialized = json.dumps(msgs, ensure_ascii=False)
    total_chars = len(serialized)
    total_tokens = estimate_tokens(serialized)

    system_chars = sum(_char_len(m.get("content")) for m in msgs if m.get("role") == "system")
    user_chars = sum(_char_len(m.get("content")) for m in msgs if m.get("role") == "user")
    assistant_chars = sum(_char_len(m.get("content")) for m in msgs if m.get("role") == "assistant")

    state_block_chars = 0
    planner_chars = 0
    semantic_chars = 0
    dependency_chars = 0
    runtime_chars = 0

    if isinstance(agent_state, dict):
        try:
            from .agent_state import (
                format_agent_state_block,
                format_dependency_block,
                format_notebook_semantic_block,
                format_plan_block,
                format_runtime_block,
            )
        except Exception:
            from agent_state import (
                format_agent_state_block,
                format_dependency_block,
                format_notebook_semantic_block,
                format_plan_block,
                format_runtime_block,
            )
        state_block_chars = _char_len(format_agent_state_block(agent_state))
        planner_chars = _char_len(format_plan_block(agent_state))
        semantic_chars = _char_len(format_notebook_semantic_block(agent_state))
        dependency_chars = _char_len(format_dependency_block(agent_state))
        runtime_chars = _char_len(format_runtime_block(agent_state))

    for m in msgs:
        content = str(m.get("content") or "")
        if "GOAL:" in content and "NOTEBOOK SEMANTIC" in content:
            state_block_chars = max(state_block_chars, len(content))

    return {
        "prompt_message_count": len(msgs),
        "prompt_total_chars": total_chars,
        "prompt_est_tokens": total_tokens,
        "system_chars": system_chars,
        "user_chars": user_chars,
        "assistant_chars": assistant_chars,
        "state_block_chars": state_block_chars,
        "planner_chars": planner_chars,
        "semantic_index_chars": semantic_chars,
        "dependency_graph_chars": dependency_chars,
        "runtime_state_chars": runtime_chars,
    }


def append_tool_refusal_record(record: dict[str, Any]) -> None:
    REFUSAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _REFUSAL_LOCK:
        with REFUSAL_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def build_tool_refusal_record(
    *,
    goal: str,
    round_idx: int,
    raw_model_response: str,
    tool_batch_found: bool,
    parsed_tool_count: int,
    prompt_tokens: int | None = None,
    response_tokens: int | None = None,
    parse_result: Any | None = None,
    messages: list[dict[str, Any]] | None = None,
    agent_state: dict[str, Any] | None = None,
    action_required: bool = True,
    session_id: str | None = None,
    notebook_url: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failure_type = classify_tool_refusal_failure(
        raw_model_response=raw_model_response,
        parse_result=parse_result,
    )
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "goal": str(goal or "").strip(),
        "round": int(round_idx),
        "raw_model_response": str(raw_model_response or ""),
        "tool_batch_found": bool(tool_batch_found),
        "parsed_tool_count": int(parsed_tool_count),
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "action_required": bool(action_required),
        "failure_type": failure_type,
        "session_id": session_id,
        "notebook_url": notebook_url,
        "parse_feedback": (
            parse_result.to_feedback_dict()
            if parse_result is not None and hasattr(parse_result, "to_feedback_dict")
            else None
        ),
        "prompt_inspection": measure_prompt_context_sizes(messages, agent_state=agent_state),
    }
    if extra:
        record.update(extra)
    return record


def log_tool_refusal_event(**kwargs: Any) -> dict[str, Any]:
    """Append one agent_tool_refusal.jsonl row; return the record."""
    record = build_tool_refusal_record(**kwargs)
    append_tool_refusal_record(record)
    return record


def log_tool_refusal_if_applicable(
    *,
    goal: str,
    round_idx: int,
    raw_model_response: str,
    parse_result: Any | None,
    parsed_tool_count: int,
    action_required: bool,
    prompt_tokens: int | None = None,
    response_tokens: int | None = None,
    messages: list[dict[str, Any]] | None = None,
    agent_state: dict[str, Any] | None = None,
    session_id: str | None = None,
    notebook_url: str | None = None,
    source: str = "react_round",
) -> dict[str, Any] | None:
    if not action_required or parsed_tool_count != 0:
        return None
    batch_count = int(getattr(parse_result, "batch_count", 0) or 0)
    has_tag = bool(_BATCH_TAG_RE.search(str(raw_model_response or "")))
    return log_tool_refusal_event(
        goal=goal,
        round_idx=round_idx,
        raw_model_response=raw_model_response,
        tool_batch_found=bool(batch_count or has_tag),
        parsed_tool_count=parsed_tool_count,
        prompt_tokens=prompt_tokens,
        response_tokens=response_tokens,
        parse_result=parse_result,
        messages=messages,
        agent_state=agent_state,
        action_required=action_required,
        session_id=session_id,
        notebook_url=notebook_url,
        extra={"source": source},
    )
