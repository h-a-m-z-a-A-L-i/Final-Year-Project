"""Generic agentic guards — no task-specific code generation."""

from __future__ import annotations

import re

_ACTION_VERBS = (
    "write", "insert", "add", "create", "edit", "fix", "run", "execute",
    "import", "load", "print", "remove", "delete", "clean", "show", "display",
    "put", "implement", "code", "verify",
)

_INSTRUCTION_MARKERS = (
    "placement",
    "run order",
    "insert below",
    "insert a new",
    "create new cell",
    "first execute cell",
    "you should run",
    "click run",
    "manually",
    "directly below cell",
)


def is_actionable_notebook_request(prompt: str) -> bool:
    text = str(prompt or "").strip().lower()
    if len(text) < 8:
        return False
    if any(v in text for v in _ACTION_VERBS):
        return True
    if re.search(r"/kaggle/input/", text):
        return True
    if re.search(r"\bcell\s*\d+\b", text):
        return True
    return False


def user_requests_run(prompt: str) -> bool:
    text = str(prompt or "").lower()
    needles = (
        " and run", " then run", " run it", " run those", " run these",
        " execute those", " execute them", " execute it", " run the cell",
        " execute the cell", " and execute", " run cell", " execute cell",
        " and run it", "now run", "run them", "run all",
    )
    if any(n in text for n in needles):
        return True
    return bool(re.search(r"\b(run|execute)\b.*\b(cells?|these|them)\b", text))


def is_write_only_request(prompt: str) -> bool:
    return is_actionable_notebook_request(prompt) and not user_requests_run(prompt)


def is_run_verify_request(prompt: str) -> bool:
    text = str(prompt or "").lower()
    return user_requests_run(prompt) and any(
        w in text for w in ("verify", "check output", "validate", "confirm")
    )


def parse_last_n_cells_request(prompt: str) -> int | None:
    """Parse 'run last 3 cells' / 'last three code cells' (digits only)."""
    text = str(prompt or "").lower()
    m = re.search(r"\blast\s+(\d+)\s+(?:code\s+)?cells?\b", text)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 50:
                return n
        except (TypeError, ValueError):
            pass
    return None


def list_code_cell_indices(registry, url: str) -> list[int]:
    try:
        listing = registry.call("notebook_list_cells", {"url": url})
    except Exception:
        return []
    if not isinstance(listing, dict):
        return []
    out: list[int] = []
    for cell in listing.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        if str(cell.get("type") or "code").lower() != "code":
            continue
        try:
            out.append(int(cell.get("index")))
        except (TypeError, ValueError):
            continue
    return out


def resolve_wanted_run_cells(
    user_prompt: str,
    parsed_calls: list,
    *,
    registry,
    url: str,
) -> list[int]:
    """
    Infer full run queue from user prompt (e.g. last N code cells).
    Merges with explicit run_cell indices from the LLM batch.
    """
    try:
        from .agent_goal_verification import extract_cell_index_from_prompt
    except Exception:
        from agent_goal_verification import extract_cell_index_from_prompt

    prompt_cell = extract_cell_index_from_prompt(user_prompt)
    if prompt_cell is not None and user_requests_run(user_prompt):
        return [int(prompt_cell)]

    explicit: list[int] = []
    for call in parsed_calls or []:
        name = getattr(call, "name", None) or (call.get("name") if isinstance(call, dict) else None)
        if name != "run_cell":
            continue
        args = getattr(call, "args", None) or (call.get("args") if isinstance(call, dict) else {}) or {}
        try:
            explicit.append(int(args.get("cell_index")))
        except (TypeError, ValueError):
            pass

    n = parse_last_n_cells_request(user_prompt)
    if n is not None and registry is not None:
        code_indices = list_code_cell_indices(registry, url)
        if len(code_indices) >= n:
            return code_indices[-n:]
    if explicit:
        return sorted(set(explicit))
    return []


def looks_like_instruction_only_response(text: str) -> bool:
    body = str(text or "").strip()
    if len(body) < 40:
        return False
    try:
        from .agentic_output_guard import contains_manual_code_without_tools, has_tool_batch_marker
    except Exception:
        from agentic_output_guard import contains_manual_code_without_tools, has_tool_batch_marker
    if contains_manual_code_without_tools(body):
        return True
    if has_tool_batch_marker(body):
        return False
    low = body.lower()
    hits = sum(1 for m in _INSTRUCTION_MARKERS if m in low)
    return hits >= 2


MAX_ERROR_RECOVERY_ROUNDS = 4
MAX_PROSE_ONLY_ROUNDS = 2


def build_prose_only_corrective_nudge(
    prompt: str,
    *,
    streak: int,
    use_text_tools: bool = False,
) -> str:
    if use_text_tools:
        lines = [
            "Your last reply had NO valid tool batch.",
            f"Original task: {prompt.strip()}",
            "You MUST emit a valid <agent_tool_batch>[...]</agent_tool_batch> JSON array "
            "with every required tool — not prose or manual instructions.",
            'Example: <agent_tool_batch>[{"tool":"edit_cell_by_index","args":{...}}]</agent_tool_batch>',
        ]
    else:
        lines = [
            "Your last reply had NO tool_calls.",
            f"Original task: {prompt.strip()}",
            "You MUST respond with native API tool_calls — every insert, edit, and run_cell "
            "in ONE assistant message. No prose, no manual code blocks.",
            "Use parallel tool_calls: multiple function entries in the same response.",
        ]
    lines.append(f"Prose-only attempt {streak} of {MAX_PROSE_ONLY_ROUNDS}. Next failure stops the agent.")
    return "\n".join(lines)


def build_prose_only_exhausted_message(prompt: str, *, streak: int, use_text_tools: bool = False) -> str:
    if use_text_tools:
        fmt = "Emit <agent_tool_batch> with edit_cell_by_index / run_cell / insert_cell as needed."
    else:
        fmt = "Emit native tool_calls (insert_cell, edit_cell_by_index, run_cell) in one response."
    return (
        f"Agent stopped: model returned prose without tools after {streak} attempt(s). "
        f"{fmt} "
        f"Original task: {prompt.strip()[:300]}"
    )


def queue_error_active(verification: dict | None) -> bool:
    if not isinstance(verification, dict):
        return False
    if verification.get("tool_queue_status") == "error":
        return True
    if verification.get("tool_queue_status") == "verification_failed":
        return True
    if verification.get("tool_queue_stopped") or verification.get("run_queue_stopped"):
        return True
    if verification.get("goal_verified") is False and verification.get("next_action_required"):
        return True
    return bool(verification.get("needs_fix") or verification.get("execution_error"))


def build_error_recovery_nudge(
    user_prompt: str,
    verification: dict,
    *,
    use_text_tools: bool = False,
    no_tools_reply: bool = False,
) -> str:
    err = verification.get("execution_error") or {}
    failed_ci = err.get("cell_index") or verification.get("cell_index")
    pending = list(verification.get("pending_run_cells") or [])
    completed = list((verification.get("tool_queue") or {}).get("run_completed") or [])
    lines = [
        "TOOL QUEUE STOPPED on a cell execution error. Do NOT give a final summary yet.",
        f"Keep the original user task in mind: {user_prompt.strip()}",
    ]
    if no_tools_reply:
        lines.insert(1, "Your last reply had no tool calls — you must inspect and/or fix via tools.")
    if failed_ci is not None:
        lines.append(f"Failed cell index: {failed_ci}")
    if err.get("error_type") or err.get("error_summary"):
        lines.append(
            f"Error: {err.get('error_type') or 'ExecutionError'}: "
            f"{(err.get('error_summary') or '')[:500]}"
        )
    if err.get("output_preview"):
        lines.append(f"Output preview: {str(err.get('output_preview'))[:600]}")
    if completed:
        lines.append(f"Runs completed before stop: {completed}")
    if pending:
        lines.append(
            f"Pending runs (skipped): {pending}. "
            "Decide if the error propagates — fix the failed cell, then include run_cell "
            "for the failed index and every pending index that depends on it."
        )
    else:
        lines.append(
            "No pending runs remain in the queue. Fix the failed cell (edit_cell_by_index), "
            "run_cell on it, then continue the original task or summarize if done."
        )
    lines.append(
        "Inspect suspect cells with notebook_get_cell or notebook_get_cells before editing."
    )
    if use_text_tools:
        lines.append(
            "Emit <agent_tool_batch>[...]</agent_tool_batch> with reads (if needed) plus "
            "edit_cell_by_index / run_cell fixes in one JSON array."
        )
    else:
        lines.append(
            "Respond with native tool_calls in one message — reads (if needed), "
            "edit_cell_by_index, and run_cell fixes together."
        )
    gate = verification.get("user_response_gate")
    if gate:
        lines.append(str(gate))
    audit = verification.get("batch_audit") or {}
    if verification.get("goal_verified") is False:
        lines.append(f"GOAL NOT VERIFIED: {verification.get('goal_reason') or audit.get('next_required_action') or 'continue repairing'}")
        failed = audit.get("failed_cells") or []
        if failed:
            lines.append(f"FAILED CELLS: {failed}")
    return "\n".join(lines)


def build_error_recovery_exhausted_message(user_prompt: str, verification: dict) -> str:
    err = verification.get("execution_error") or {}
    failed_ci = err.get("cell_index") or verification.get("cell_index")
    pending = verification.get("pending_run_cells") or []
    msg = (
        f"Stopped after repeated error-recovery attempts. "
        f"Cell {failed_ci} failed"
    )
    if err.get("error_summary"):
        msg += f": {err.get('error_summary')}"
    msg += "."
    if pending:
        msg += f" Did not run pending cells {pending}."
    msg += f" Original task: {user_prompt.strip()[:300]}"
    return msg


def agentic_must_continue_with_tools(
    *,
    prompt: str,
    followup_text: str,
    tools_executed: int,
    pipeline_active: bool,
    queue_error_active_flag: bool = False,
) -> bool:
    if queue_error_active_flag:
        return True
    if pipeline_active:
        return True
    if not is_actionable_notebook_request(prompt):
        return False
    if tools_executed == 0:
        return True
    return looks_like_instruction_only_response(followup_text)


def build_action_nudge(
    prompt: str,
    *,
    tools_executed: int,
    round_idx: int,
    use_text_tools: bool = False,
) -> str:
    if use_text_tools:
        fmt = "Emit <agent_tool_batch>[...]</agent_tool_batch> with required tools."
    else:
        fmt = (
            "Respond with native API tool_calls in ONE message (parallel enabled) — "
            "not prose or manual instructions."
        )
    return (
        f"Agentic mode: {fmt} "
        f"tools_executed={tools_executed}, round={round_idx}.\n"
        f"Task: {prompt.strip()}"
    )
