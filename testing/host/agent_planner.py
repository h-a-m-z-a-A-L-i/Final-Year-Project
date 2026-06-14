"""Lightweight host-managed workflow planner for multi-step notebook tasks."""

from __future__ import annotations

import json
import os
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT
except Exception:
    from config import DATA_ROOT

PLAN_MEMORY_PATH = DATA_ROOT / "meta" / "agent_plan_memory.json"
_PLAN_LOCK = threading.Lock()

_MULTI_STEP_MARKERS = (
    "pipeline",
    "workflow",
    "step 1",
    "step 2",
    "multi-step",
    "multistep",
    "titanic",
    "cnn",
    "eda",
    "exploratory",
    "feature engineering",
    "train and evaluate",
    "train model",
    "visualization",
    "end-to-end",
    "full notebook",
)

_PLAN_HEADER = re.compile(r"^\s*PLAN\s*:\s*$", re.IGNORECASE | re.MULTILINE)
_PLAN_NUMBERED = re.compile(
    r"^\s*(?:\d+[\.\)]\s*|-\s+|\*\s+)(.+)$",
    re.MULTILINE,
)


def planner_enabled() -> bool:
    raw = os.environ.get("AGENTIC_PLANNER", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def needs_explicit_plan(prompt: str) -> bool:
    """True when the user request is a large multi-step notebook workflow."""
    text = str(prompt or "").strip().lower()
    if len(text) < 40:
        return False
    if any(m in text for m in _MULTI_STEP_MARKERS):
        return True
    if text.count(" and ") >= 2 or text.count(",") >= 3:
        return True
    if re.search(r"\b(then|after that|next|finally)\b", text) and len(text) > 80:
        return True
    return False


def parse_plan_from_text(text: str) -> list[str]:
    """Extract numbered plan steps from LLM prose (PLAN: block or numbered list)."""
    raw = str(text or "").strip()
    if not raw:
        return []

    block = raw
    m = _PLAN_HEADER.search(raw)
    if m:
        block = raw[m.end() :].strip()
        if "<agent_tool_batch>" in block:
            block = block.split("<agent_tool_batch>", 1)[0].strip()

    steps: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("PLAN"):
            continue
        match = _PLAN_NUMBERED.match(line)
        if match:
            step = match.group(1).strip()
            if step and step not in steps:
                steps.append(step)
        elif steps and not line.startswith("<"):
            steps[-1] = f"{steps[-1]} {line}".strip()

    if steps:
        return steps

    for match in _PLAN_NUMBERED.finditer(raw):
        step = match.group(1).strip()
        if step and step not in steps:
            steps.append(step)
    return steps


def build_plan_request_nudge(prompt: str) -> str:
    return (
        "Before executing any tools, respond with a short numbered workflow plan only.\n\n"
        "Format:\n"
        "PLAN:\n"
        "1. First step …\n"
        "2. Second step …\n"
        "3. …\n\n"
        f"Original task: {prompt.strip()}\n\n"
        "Do NOT emit <agent_tool_batch> in this reply — plan only."
    )


def build_step_execution_nudge(state: dict[str, Any]) -> str:
    idx = state.get("current_step")
    plan = list(state.get("plan") or [])
    if idx is None or not plan:
        return (
            "Plan stored. Emit one <agent_tool_batch> with all tools needed for the "
            "current workflow step."
        )
    step_n = int(idx) + 1
    current = plan[int(idx)] if 0 <= int(idx) < len(plan) else ""
    pending = list(state.get("pending_steps") or [])
    return (
        f"Execute ONLY the current plan step ({step_n}/{len(plan)}):\n"
        f"CURRENT STEP: {current}\n\n"
        f"Remaining after this: {pending[1:] if len(pending) > 1 else 'none'}\n\n"
        "Emit one <agent_tool_batch> with every tool required for this step only."
    )


def sync_plan_pending_steps(state: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(state) if isinstance(state, dict) else {}
    plan = list(out.get("plan") or [])
    completed = set(out.get("plan_completed_indices") or [])
    out["pending_steps"] = [plan[i] for i in range(len(plan)) if i not in completed]
    if plan and out.get("current_step") is None and completed != set(range(len(plan))):
        for i in range(len(plan)):
            if i not in completed:
                out["current_step"] = i
                break
    elif plan and out.get("current_step") is not None:
        idx = int(out["current_step"])
        if idx in completed:
            for i in range(len(plan)):
                if i not in completed:
                    out["current_step"] = i
                    break
            else:
                out["current_step"] = None
    return out


def apply_plan_from_llm_response(
    state: dict[str, Any] | None,
    text: str,
    *,
    goal: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """Parse PLAN from LLM text into host state. Returns (state, steps)."""
    steps = parse_plan_from_text(text)
    out = deepcopy(state) if isinstance(state, dict) else {}
    if goal and not out.get("goal"):
        out["goal"] = str(goal).strip()
    if not steps:
        return out, []
    out["plan"] = steps
    out["plan_completed_indices"] = []
    out["current_step"] = 0
    out["completed_steps"] = []
    out["pending_steps"] = list(steps)
    out["plan_created_at"] = True
    return sync_plan_pending_steps(out), steps


def planning_phase_active(state: dict[str, Any] | None, *, prompt: str) -> bool:
    if not planner_enabled() or not needs_explicit_plan(prompt):
        return False
    if not isinstance(state, dict):
        return True
    return not bool(state.get("plan"))


def current_plan_step_label(state: dict[str, Any] | None) -> str | None:
    if not isinstance(state, dict):
        return None
    plan = state.get("plan") or []
    idx = state.get("current_step")
    if idx is None or not plan:
        return None
    i = int(idx)
    if 0 <= i < len(plan):
        return str(plan[i])
    return None


def update_plan_from_verification(
    state: dict[str, Any] | None,
    verification: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """
    Advance or fail the current plan step based on batch verification.
    Returns (updated_state, metric_event) where metric_event is one of:
    step_completed, step_failed, step_retried, or None.
    """
    out = deepcopy(state) if isinstance(state, dict) else {}
    plan = list(out.get("plan") or [])
    if not plan:
        return out, None

    idx = out.get("current_step")
    if idx is None:
        out = sync_plan_pending_steps(out)
        return out, None

    step_idx = int(idx)
    completed_indices = list(out.get("plan_completed_indices") or [])
    metric_event: str | None = None

    err = verification.get("execution_error") or {}
    has_error = bool(
        verification.get("needs_fix")
        or err
        or verification.get("tool_queue_status") == "error"
    )

    if has_error:
        out["last_error"] = {
            "plan_step_index": step_idx,
            "plan_step": plan[step_idx] if 0 <= step_idx < len(plan) else None,
            "cell_index": err.get("cell_index") or verification.get("cell_index"),
            "error_type": err.get("error_type"),
            "error_summary": err.get("error_summary"),
            "pending_run_cells": list(verification.get("pending_run_cells") or []),
            "required_action": (
                f"Fix and retry plan step {step_idx + 1} only: "
                f"{plan[step_idx] if 0 <= step_idx < len(plan) else '?'}"
            ),
        }
        out["_step_retry_pending"] = True
        metric_event = "step_failed"
        return sync_plan_pending_steps(out), metric_event

    success = bool(
        verification.get("verified")
        or verification.get("tool_queue_complete")
        or verification.get("run_queue_complete")
        or verification.get("batch_executed")
    )
    if not success:
        return sync_plan_pending_steps(out), None

    was_retry = bool(out.pop("_step_retry_pending", None))
    label = plan[step_idx] if 0 <= step_idx < len(plan) else f"step_{step_idx + 1}"
    if step_idx not in completed_indices:
        completed_indices.append(step_idx)
        completed_indices.sort()
    out["plan_completed_indices"] = completed_indices
    step_key = f"plan:{step_idx + 1}:{label}"
    tool_steps = list(out.get("completed_steps") or [])
    if step_key not in tool_steps:
        tool_steps.append(step_key)
    out["completed_steps"] = tool_steps
    out["last_error"] = None
    metric_event = "step_retried" if was_retry else "step_completed"

    for i in range(len(plan)):
        if i not in completed_indices:
            out["current_step"] = i
            break
    else:
        out["current_step"] = None

    return sync_plan_pending_steps(out), metric_event


def _load_plan_memory() -> dict[str, Any]:
    if not PLAN_MEMORY_PATH.is_file():
        return {}
    try:
        data = json.loads(PLAN_MEMORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_plan_memory(data: dict[str, Any]) -> None:
    PLAN_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_MEMORY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def persist_agent_plan(history_key: str, state: dict[str, Any] | None) -> None:
    if not history_key or not isinstance(state, dict) or not state.get("plan"):
        return
    payload = {
        "goal": state.get("goal"),
        "plan": state.get("plan"),
        "current_step": state.get("current_step"),
        "plan_completed_indices": state.get("plan_completed_indices"),
        "completed_steps": state.get("completed_steps"),
        "pending_steps": state.get("pending_steps"),
        "last_error": state.get("last_error"),
    }
    with _PLAN_LOCK:
        mem = _load_plan_memory()
        mem[str(history_key)] = payload
        _save_plan_memory(mem)


def load_agent_plan(history_key: str, *, goal: str = "") -> dict[str, Any] | None:
    if not history_key:
        return None
    with _PLAN_LOCK:
        mem = _load_plan_memory()
        raw = mem.get(str(history_key))
    if not isinstance(raw, dict) or not raw.get("plan"):
        return None
    out = {
        "goal": raw.get("goal") or goal,
        "plan": list(raw.get("plan") or []),
        "current_step": raw.get("current_step"),
        "plan_completed_indices": list(raw.get("plan_completed_indices") or []),
        "completed_steps": list(raw.get("completed_steps") or []),
        "pending_steps": list(raw.get("pending_steps") or []),
        "last_error": raw.get("last_error"),
        "current_plan": [],
        "pipeline_state": {},
    }
    return sync_plan_pending_steps(out)


def clear_agent_plan(history_key: str) -> None:
    if not history_key:
        return
    with _PLAN_LOCK:
        mem = _load_plan_memory()
        if str(history_key) in mem:
            del mem[str(history_key)]
            _save_plan_memory(mem)
