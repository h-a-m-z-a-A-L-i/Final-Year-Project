"""Host-managed agent state outside SQLite chat history."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

REACT_STATE_MARKER = "__react_agent_state__"
REACT_ORIGINAL_USER = "_react_original_user"
REACT_TOOL_BATCH = "_react_tool_batch"
REACT_VERIFICATION = "_react_verification"

COMPLETED_STEPS_COMPRESS_AFTER = 10
COMPLETED_STEPS_RECENT_TAIL = 3


def empty_agent_state(*, goal: str = "") -> dict[str, Any]:
    return {
        "goal": str(goal or "").strip(),
        "plan": [],
        "current_step": None,
        "plan_completed_indices": [],
        "completed_steps": [],
        "pending_steps": [],
        "last_error": None,
        "current_plan": [],
        "pipeline_state": {},
        "notebook_key": "",
        "notebook_semantic": {},
    }


def update_agent_state_from_verification(
    state: dict[str, Any] | None,
    verification: dict[str, Any],
    *,
    goal: str = "",
) -> dict[str, Any]:
    out = deepcopy(state) if isinstance(state, dict) else empty_agent_state(goal=goal)
    if goal and not out.get("goal"):
        out["goal"] = str(goal).strip()

    has_workflow_plan = bool(out.get("plan"))

    if not has_workflow_plan:
        executed = verification.get("executed") or []
        for item in executed:
            if not isinstance(item, dict):
                continue
            step = f"{item.get('tool')}:{item.get('cell_index') or item.get('phase') or 'ok'}"
            if step not in out["completed_steps"]:
                out["completed_steps"].append(step)

        pending: list[str] = []
        for ci in verification.get("pending_run_cells") or []:
            pending.append(f"run_cell:{ci}")
        for d in verification.get("deferred_tool_calls") or []:
            if isinstance(d, dict):
                pending.append(f"{d.get('tool')}:{d.get('args', {}).get('cell_index', '?')}")
        pipeline = verification.get("pipeline") or {}
        for ci in pipeline.get("pending_runs") or []:
            step = f"run_cell:{ci}"
            if step not in pending:
                pending.append(step)
        out["pending_steps"] = pending

    err = verification.get("execution_error") or {}
    if not isinstance(err, dict):
        err = {"error_summary": str(err)} if err else {}
    if not has_workflow_plan:
        if verification.get("needs_fix") or err:
            out["last_error"] = {
                "cell_index": err.get("cell_index") or verification.get("cell_index"),
                "error_type": err.get("error_type"),
                "error_summary": err.get("error_summary"),
                "pending_run_cells": list(verification.get("pending_run_cells") or []),
                "required_action": verification.get("user_response_gate") or (
                    "Inspect failed cell, edit_cell_by_index, re-run failed and pending cells."
                ),
            }
        elif verification.get("tool_queue_complete") or verification.get("tool_queue_status") == "complete":
            out["last_error"] = None

    if verification.get("pipeline"):
        out["pipeline_state"] = dict(verification.get("pipeline") or {})

    plan_ops: list[str] = []
    if out.get("last_error") and not has_workflow_plan:
        plan_ops.append("fix_error")
    if not has_workflow_plan:
        for p in out.get("pending_steps") or []:
            plan_ops.append(p)
    if verification.get("await_llm_summary") or verification.get("tool_queue_complete"):
        plan_ops.append("summarize")
    out["current_plan"] = plan_ops
    _compress_completed_steps(out)

    try:
        from .notebook_semantic_index import sync_semantic_index_to_agent_state
    except Exception:
        from notebook_semantic_index import sync_semantic_index_to_agent_state
    out = sync_semantic_index_to_agent_state(out, verification)

    try:
        from .notebook_dependency_graph import sync_dependency_graph_to_agent_state
    except Exception:
        from notebook_dependency_graph import sync_dependency_graph_to_agent_state
    out = sync_dependency_graph_to_agent_state(out, verification)

    try:
        from .runtime_state import sync_runtime_state_to_agent_state
    except Exception:
        from runtime_state import sync_runtime_state_to_agent_state
    out = sync_runtime_state_to_agent_state(out, verification)

    return out


def _compress_completed_steps(state: dict[str, Any]) -> None:
    steps = list(state.get("completed_steps") or [])
    n = len(steps)
    if n <= COMPLETED_STEPS_COMPRESS_AFTER:
        return
    state["completed_steps_summary"] = f"{n} steps completed"
    state["completed_steps"] = steps[-COMPLETED_STEPS_RECENT_TAIL:]
    state["completed_steps_compressed"] = True


def format_completed_steps_block(state: dict[str, Any]) -> str:
    completed = state.get("completed_steps") or []
    if not completed:
        return ""
    summary = state.get("completed_steps_summary")
    if summary and state.get("completed_steps_compressed"):
        recent = "\n- ".join(str(x) for x in completed)
        return f"COMPLETED STEPS:\n{summary}\n\nRecent:\n- {recent}"
    return f"COMPLETED STEPS:\n- " + "\n- ".join(str(x) for x in completed[-12:])


def format_plan_block(state: dict[str, Any]) -> str:
    plan = state.get("plan") or []
    if not plan:
        return ""
    lines = ["WORKFLOW PLAN:"]
    completed = set(state.get("plan_completed_indices") or [])
    for i, step in enumerate(plan):
        mark = "[x]" if i in completed else "[ ]"
        cur = " <- CURRENT" if state.get("current_step") == i else ""
        lines.append(f"{mark} {i + 1}. {step}{cur}")
    return "\n".join(lines)


def format_notebook_semantic_block(state: dict[str, Any]) -> str:
    try:
        from .notebook_semantic_index import format_semantic_index_block
    except Exception:
        from notebook_semantic_index import format_semantic_index_block
    full = state.get("_semantic_index_full")
    if isinstance(full, dict):
        return format_semantic_index_block(full)
    return format_semantic_index_block(state.get("notebook_semantic"))


def format_dependency_block(state: dict[str, Any]) -> str:
    summary = state.get("_dependency_summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    try:
        from .notebook_dependency_graph import format_dependency_summary
    except Exception:
        from notebook_dependency_graph import format_dependency_summary
    full = state.get("_dependency_graph_full")
    if isinstance(full, dict):
        return format_dependency_summary(full)
    return ""


def format_runtime_block(state: dict[str, Any]) -> str:
    summary = state.get("_runtime_summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    try:
        from .runtime_state import format_runtime_state_block
    except Exception:
        from runtime_state import format_runtime_state_block
    full = state.get("_runtime_state_full")
    if isinstance(full, dict):
        return format_runtime_state_block(full)
    return ""


def format_agent_state_block(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    lines = [REACT_STATE_MARKER]
    goal = str(state.get("goal") or "").strip()
    if goal:
        lines.append(f"GOAL:\n{goal}")

    notebook_block = format_notebook_semantic_block(state)
    if notebook_block:
        lines.append(notebook_block)

    dep_block = format_dependency_block(state)
    if dep_block:
        lines.append(dep_block)

    runtime_block = format_runtime_block(state)
    if runtime_block:
        lines.append(runtime_block)

    plan_block = format_plan_block(state)
    if plan_block:
        lines.append(plan_block)
        idx = state.get("current_step")
        plan = state.get("plan") or []
        if idx is not None and plan and 0 <= int(idx) < len(plan):
            lines.append(f"CURRENT STEP:\n{int(idx) + 1}. {plan[int(idx)]}")

    completed_block = format_completed_steps_block(state)
    if completed_block:
        lines.append(completed_block)

    pending = state.get("pending_steps") or []
    if pending:
        label = "PENDING STEPS:" if state.get("plan") else "PENDING:"
        lines.append(f"{label}\n- " + "\n- ".join(str(x) for x in pending))

    err = state.get("last_error")
    if isinstance(err, dict) and err:
        lines.append("LAST ERROR:")
        if err.get("plan_step_index") is not None:
            lines.append(f"FAILED PLAN STEP: {int(err['plan_step_index']) + 1}")
        if err.get("plan_step"):
            lines.append(f"STEP: {err.get('plan_step')}")
        if err.get("cell_index") is not None:
            lines.append(f"FAILED CELL: {err.get('cell_index')}")
        if err.get("error_summary"):
            lines.append(f"ERROR: {err.get('error_summary')}")
        if err.get("pending_run_cells"):
            lines.append(f"PENDING CELLS: {err.get('pending_run_cells')}")
        if err.get("required_action"):
            lines.append(f"REQUIRED ACTION: {str(err.get('required_action'))[:500]}")
        dr = err.get("dependency_repair")
        if isinstance(dr, dict) and dr.get("hint"):
            lines.append(f"DEPENDENCY REPAIR: {dr['hint'][:400]}")
        rc = err.get("runtime_context")
        if isinstance(rc, dict) and rc.get("relevant"):
            lines.append("Relevant runtime:")
            for item in rc["relevant"][:5]:
                lines.append(f"- {item}")

    pipeline = state.get("pipeline_state") or {}
    if isinstance(pipeline, dict) and pipeline.get("active"):
        lines.append(
            f"PIPELINE: pending_runs={pipeline.get('pending_runs')} "
            f"completed={pipeline.get('completed_runs')}"
        )
    return "\n\n".join(lines)


def inject_agent_state_message(
    messages: list[dict[str, Any]],
    state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Insert compact agent state before the latest user/tool tail (for LLM visibility)."""
    if isinstance(state, dict):
        try:
            from .notebook_semantic_index import semantic_index_enabled, sync_semantic_index_to_agent_state
        except Exception:
            from notebook_semantic_index import semantic_index_enabled, sync_semantic_index_to_agent_state
        if semantic_index_enabled() and not state.get("_semantic_index_full"):
            state = sync_semantic_index_to_agent_state(state, None)
        try:
            from .notebook_dependency_graph import dependency_graph_enabled, sync_dependency_graph_to_agent_state
        except Exception:
            from notebook_dependency_graph import dependency_graph_enabled, sync_dependency_graph_to_agent_state
        if dependency_graph_enabled() and not state.get("_dependency_graph_full"):
            state = sync_dependency_graph_to_agent_state(state, None)
        try:
            from .runtime_state import runtime_state_enabled, sync_runtime_state_to_agent_state
        except Exception:
            from runtime_state import runtime_state_enabled, sync_runtime_state_to_agent_state
        if runtime_state_enabled() and not state.get("_runtime_state_full"):
            state = sync_runtime_state_to_agent_state(state, None)
    block = format_agent_state_block(state)
    if not block:
        return messages
    out = list(messages)
    insert_at = len(out)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get(REACT_VERIFICATION) or out[i].get(REACT_TOOL_BATCH):
            insert_at = i + 1
            break
    out.insert(
        insert_at,
        {
            "role": "user",
            "content": block,
            "_react_agent_state": True,
        },
    )
    return out
