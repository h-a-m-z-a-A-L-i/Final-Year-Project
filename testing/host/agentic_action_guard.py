"""Generic agentic guards — no task-specific code generation."""

from __future__ import annotations

import re

_ACTION_VERBS = (
    "write", "insert", "add", "create", "edit", "fix", "run", "execute",
    "import", "load", "print", "remove", "delete", "clean", "show", "display",
    "put", "implement", "code", "verify", "make", "predict", "train", "build",
)

_QUERY_ONLY_TOOLS = frozenset({
    "notebook_get_cell",
    "notebook_get_cells",
    "notebook_find_symbol",
    "notebook_recommend_placement",
    "notebook_list_cells",
    "notebook_graph_query",
    "notebook_search",
    "notebook_overview",
    "notebook_executed_cells",
    "notebook_snapshot_status",
    "notebook_cell_neighbors",
})

_WRITE_TOOLS = frozenset({
    "insert_cell",
    "edit_cell_by_index",
    "delete_by_index",
    "creating_markdown_by_index",
    "run_cell",
    "run_all_cells",
    "select_cell_by_index",
    "click_cell",
})

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


def prompt_requests_split_cell(prompt: str) -> bool:
    try:
        from .agentic_tool_chain import prompt_requests_split_cell as _split
    except Exception:
        from agentic_tool_chain import prompt_requests_split_cell as _split
    return _split(prompt)


def _split_source_cell_index(prompt: str) -> int | None:
    try:
        from .agentic_tool_chain import (
            extract_insert_anchor_from_prompt,
            extract_target_cell_index,
        )
    except Exception:
        from agentic_tool_chain import (
            extract_insert_anchor_from_prompt,
            extract_target_cell_index,
        )
    return extract_target_cell_index(prompt) or extract_insert_anchor_from_prompt(prompt)


def batch_has_split_source_read(prompt: str, tool_calls: list | None) -> bool:
    """True when a split/divide batch already read the source cell (or context is enough)."""
    if not prompt_requests_split_cell(prompt):
        return True
    target = _split_source_cell_index(prompt)
    if target is None:
        return False
    for call in tool_calls or []:
        name = getattr(call, "name", None) or (
            call.get("name") if isinstance(call, dict) else None
        )
        args = getattr(call, "args", None) or (
            call.get("args") if isinstance(call, dict) else {}
        ) or {}
        if name == "notebook_get_cell":
            try:
                if int(args.get("cell_index")) == int(target):
                    return True
            except (TypeError, ValueError):
                pass
        elif name == "notebook_get_cells":
            raw_indices = args.get("cell_indices") or []
            try:
                indices = {int(i) for i in raw_indices}
            except (TypeError, ValueError):
                indices = set()
            if int(target) in indices:
                return True
    return False


def split_cell_write_without_source(prompt: str, tool_calls: list | None) -> bool:
    """Split/divide requests with writes but no read of the source cell."""
    if not prompt_requests_split_cell(prompt):
        return False
    names = []
    for call in tool_calls or []:
        name = getattr(call, "name", None) or (
            call.get("name") if isinstance(call, dict) else None
        )
        if name:
            names.append(str(name))
    if not any(n in _WRITE_TOOLS for n in names):
        return False
    return not batch_has_split_source_read(prompt, tool_calls)


def build_split_cell_source_nudge(prompt: str) -> str:
    target = _split_source_cell_index(prompt)
    cell_hint = f"cell {target}" if target is not None else "the source cell"
    return (
        f"Split/divide request requires the original source from {cell_hint}. "
        "Call notebook_get_cell on that index first (or use full source already in Context), "
        "then insert_cell (empty) and edit_cell_by_index with portions of the ORIGINAL code. "
        "Never use print(1), print(2) placeholders or invent new code.\n"
        f"Task: {prompt.strip()[:300]}"
    )


def is_query_only_tool_batch(parsed_tools: list[str] | None) -> bool:
    """True when every parsed tool is a local read/query tool (no writes or runs)."""
    names = [str(t).strip() for t in (parsed_tools or []) if str(t).strip()]
    if not names:
        return False
    return all(n in _QUERY_ONLY_TOOLS for n in names)


def batch_lacks_write_tools(parsed_tools: list[str] | None) -> bool:
    names = [str(t).strip() for t in (parsed_tools or []) if str(t).strip()]
    if not names:
        return True
    return not any(n in _WRITE_TOOLS for n in names)


def is_implementation_request(prompt: str) -> bool:
    """Actionable notebook work that must modify cells — not pure Q&A."""
    if not is_actionable_notebook_request(prompt):
        return False
    low = str(prompt or "").strip().lower()
    build_signals = (
        "import", "load", "model", "regression", "predict", "train",
        "insert", "create", "make", "add", "edit", "fix", "implement",
        "linear", "sklearn", "performance", "accuracy", "metric",
    )
    return any(sig in low for sig in build_signals)


def count_implied_tool_actions(prompt: str) -> int:
    """Heuristic: how many distinct write/run tools the user message implies."""
    text = str(prompt or "").strip()
    if not text:
        return 0
    low = text.lower()
    count = 0
    try:
        from .agentic_tool_chain import (
            prompt_requests_delete,
            prompt_requests_insert,
        )
    except Exception:
        from agentic_tool_chain import (
            prompt_requests_delete,
            prompt_requests_insert,
        )
    if prompt_requests_delete(text):
        count += 1
    new_cell_hits = len(
        re.findall(r"\b(?:make|create|add)\s+(?:a\s+)?new\s+cell\b", low)
    )
    if new_cell_hits:
        count += new_cell_hits
    elif prompt_requests_insert(text):
        count += 1
    if re.search(r"\b(?:edit|fix|update|change|modify)\b.*\bcell\b", low) or re.search(
        r"\bcell\s*\d+\b.*\b(?:edit|fix|update|change|modify)\b", low
    ):
        count += 1
    if user_requests_run(text):
        count += 1
    if is_implementation_request(text) and count < 2:
        workflow_hits = sum(
            1
            for sig in (
                "import", "load", "model", "regression", "predict",
                "performance", "metric", "train", "sklearn",
            )
            if sig in low
        )
        if workflow_hits >= 2:
            count = max(count, 3)
        elif workflow_hits == 1 and ("cell" in low or "new cell" in low):
            count = max(count, 2)
    return count


def build_query_only_rejection_message(
    prompt: str,
    *,
    parsed_tools: list[str] | None = None,
) -> str:
    got = ", ".join(parsed_tools or []) or "read tools only"
    return (
        "Implementation requests must use write tools "
        "(insert_cell, edit_cell_by_index, run_cell) in one batch — "
        f"not {got} alone. Context already includes cell indices; "
        "skip notebook_list_cells. Retry the request.\n"
        f"Task: {prompt.strip()[:300]}"
    )


def is_query_tool(name: str) -> bool:
    return str(name or "").strip() in _QUERY_ONLY_TOOLS


def cumulative_has_write_tools(executed: list[dict] | None) -> bool:
    """True when any dispatched tool in cumulative fire-and-forget state was a write/run."""
    write_names = _WRITE_TOOLS
    for row in executed or []:
        if not isinstance(row, dict):
            continue
        tool = str(row.get("tool") or "").strip()
        if tool in write_names:
            return True
    return False


def should_force_implementation_batch(
    *,
    prompt: str,
    parsed_tools: list[str] | None,
    query_rounds_used: int,
    max_query_rounds: int,
    cumulative_has_writes: bool,
    round_idx: int | None = None,
    max_tool_rounds: int | None = None,
) -> bool:
    """Replace query-only LLM batches with host writes after query budget or on final API call."""
    if cumulative_has_writes:
        return False
    if not is_actionable_notebook_request(prompt):
        return False
    if not is_query_only_tool_batch(parsed_tools):
        return False
    if int(query_rounds_used) >= int(max_query_rounds):
        return True
    if round_idx is not None and max_tool_rounds is not None:
        if int(round_idx) >= int(max_tool_rounds) - 1:
            return True
    return False


def build_query_budget_exhausted_nudge(
    prompt: str,
    *,
    parsed_tools: list[str] | None = None,
) -> str:
    got = ", ".join(parsed_tools or []) or "read tools only"
    return (
        "Query budget exhausted — you already used your one optional read round. "
        f"Do not call {got} again. "
        "Dispatch insert_cell, edit_cell_by_index, and run_cell now in one tool_calls batch.\n"
        f"Task: {prompt.strip()[:300]}"
    )


def build_query_loop_exhausted_message(prompt: str) -> str:
    return (
        "Could not plan implementation — query-only rounds exhausted without dispatching "
        "insert_cell, edit_cell_by_index, or run_cell. "
        "Retry with explicit cell indices or a smaller task.\n"
        f"Task: {prompt.strip()[:300]}"
    )


MAX_INCOMPLETE_BATCH_NUDGES = 1


def build_incomplete_batch_nudge(
    prompt: str,
    *,
    parsed_count: int,
    implied_count: int,
    parsed_tools: list[str] | None = None,
    use_text_tools: bool = False,
) -> str:
    missing = max(0, implied_count - parsed_count)
    got = ", ".join(parsed_tools or []) or "(none)"
    lines = [
        f"Incomplete tool batch: user request implies ~{implied_count} tool action(s), "
        f"but you returned {parsed_count} ({got}).",
        f"Original task: {prompt.strip()}",
        f"Emit ALL {implied_count}+ required tools in ONE response — {missing} more still needed.",
    ]
    if use_text_tools:
        lines.append(
            "Use <agent_tool_batch>[...]</agent_tool_batch> with every missing tool in one JSON array."
        )
    else:
        lines.append(
            "Use native API tool_calls with every missing function in one assistant message "
            "(parallel_tool_calls enabled)."
        )
    return "\n".join(lines)


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
    batch_err = verification.get("batch_error_context") or {}
    succeeded = batch_err.get("succeeded_operations") or []
    if succeeded:
        lines.append("Other operations in this batch that succeeded:")
        for op in succeeded[:8]:
            if not isinstance(op, dict):
                continue
            ci = op.get("cell_index")
            tool = op.get("tool") or "?"
            preview = op.get("output_preview") or op.get("input_preview") or op.get("args_preview") or ""
            lines.append(f"  - {tool} cell {ci}: {str(preview)[:200]}")
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
