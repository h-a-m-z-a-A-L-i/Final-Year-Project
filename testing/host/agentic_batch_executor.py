"""Host-controlled agentic tool batch: dispatch, wait, verify, one LLM payload."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from .strict_execution_engine import (
        EXECUTE_FULL_BATCH_WITHOUT_LLM_SPLIT,
        attach_strict_execution,
        build_execution_queue,
        build_run_cell_result,
        record_queue_progress,
    )
except Exception:
    from strict_execution_engine import (
        EXECUTE_FULL_BATCH_WITHOUT_LLM_SPLIT,
        attach_strict_execution,
        build_execution_queue,
        build_run_cell_result,
        record_queue_progress,
    )

try:
    from .agentic_tool_chain import (
        build_edit_after_insert,
        extract_cell_count_from_prompt,
        extract_insert_anchor_from_prompt,
        infer_new_cell_index,
        parse_multi_cell_contents,
    )
    from .notebook_context import load_notebook_snapshot
    from .snapshot_verification import cells_from_snapshot, snapshot_fingerprint
except Exception:
    from agentic_tool_chain import (
        build_edit_after_insert,
        extract_cell_count_from_prompt,
        extract_insert_anchor_from_prompt,
        infer_new_cell_index,
        parse_multi_cell_contents,
    )
    from notebook_context import load_notebook_snapshot
    from snapshot_verification import cells_from_snapshot, snapshot_fingerprint

INTER_TOOL_DELAY_SEC = 0.5
INSERT_SETTLE_SEC = 0.8
POST_BATCH_SETTLE_SEC = 1.5
RUN_WAIT_TIMEOUT_SEC = 120.0
RUN_POLL_INTERVAL_SEC = 1.0
SNAPSHOT_CHANGE_TIMEOUT_SEC = 45.0
SNAPSHOT_POLL_INTERVAL_SEC = 0.5
MAX_CELL_OUTPUT_CHARS = 8000

_CELL_ERROR_LINE = re.compile(
    r"\b("
    r"Traceback \(most recent call last\)|"
    r"NameError|TypeError|KeyError|IndexError|ValueError|AttributeError|"
    r"ImportError|ModuleNotFoundError|SyntaxError|RuntimeError|ZeroDivisionError|"
    r"FileNotFoundError|RecursionError|AssertionError|StopIteration|"
    r"Cell execution failed|Execution error|Exception:"
    r")\b",
    re.IGNORECASE,
)
_CELL_ERROR_TYPE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):\s*(.+)$",
)


def analyze_cell_output(output: str | None) -> dict[str, Any]:
    """Detect kernel/traceback errors in scraped cell output."""
    text = str(output or "").strip()
    if not text:
        return {
            "has_output": False,
            "has_error": False,
            "run_succeeded": False,
            "pending": True,
            "error_type": None,
            "error_summary": None,
        }

    has_error = bool(_CELL_ERROR_LINE.search(text))
    error_type = None
    error_summary = None

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in reversed(lines):
        match = _CELL_ERROR_TYPE.match(line)
        if match:
            error_type = match.group(1)
            error_summary = line
            has_error = True
            break

    if has_error and not error_summary:
        for line in reversed(lines):
            if _CELL_ERROR_LINE.search(line):
                error_summary = line[:500]
                break
        if not error_summary and "traceback" in text.lower():
            error_summary = lines[-1][:500] if lines else text[:500]

    return {
        "has_output": True,
        "has_error": has_error,
        "run_succeeded": not has_error,
        "error_type": error_type,
        "error_summary": error_summary,
        "output_preview": text[:1500],
    }


def workflow_needs_llm_followup(verification: dict[str, Any]) -> bool:
    """True when the ReAct loop must call the LLM again before answering the user."""
    if not isinstance(verification, dict):
        return False
    pipeline = verification.get("pipeline") or {}
    deferred = verification.get("deferred_tool_calls") or []
    pending_runs = list(verification.get("pending_run_cells") or [])
    status = verification.get("tool_queue_status")

    if not verification.get("needs_fix") and not verification.get("execution_error"):
        queue_done = (
            status == "complete"
            or verification.get("tool_queue_complete")
            or verification.get("run_queue_complete")
        )
        if queue_done and not pending_runs and not deferred:
            return False

    if pipeline.get("active") and not pipeline.get("complete"):
        return True
    if verification.get("pipeline_active"):
        return True
    if status == "error" or verification.get("tool_queue_stopped") or verification.get("run_queue_stopped"):
        return True
    if verification.get("needs_fix"):
        return True
    if verification.get("runs_incomplete"):
        return True
    if deferred:
        if any(d.get("tool") != "run_cell" for d in deferred):
            return True
    if verification.get("execution_error"):
        return True
    try:
        from .agent_goal_verification import goal_verification_failed
    except Exception:
        from agent_goal_verification import goal_verification_failed
    if goal_verification_failed(verification):
        return True
    if verification.get("run_completed") and pipeline.get("active") and not pipeline.get("complete"):
        return True
    if verification.get("verified") is True:
        return False
    return True


def workflow_followup_reason(verification: dict[str, Any]) -> str:
    """Human-readable reason for continue/stop (observability)."""
    if not isinstance(verification, dict):
        return "invalid_verification"
    if not workflow_needs_llm_followup(verification):
        return "queue_complete_no_pending_go_to_final_summary"
    try:
        from .agent_goal_verification import goal_verification_failed
    except Exception:
        from agent_goal_verification import goal_verification_failed
    if goal_verification_failed(verification):
        return "goal_verification_failed"
    if verification.get("needs_fix") or verification.get("execution_error"):
        return "error_recovery"
    if verification.get("deferred_tool_calls"):
        return "deferred_tools"
    if verification.get("pending_run_cells"):
        return "pending_runs"
    pipeline = verification.get("pipeline") or {}
    if pipeline.get("active") and not pipeline.get("complete"):
        return "pipeline_active"
    return "continue_react"


def user_requests_run(user_prompt: str, parsed_calls: list[ParsedToolCall] | None = None) -> bool:
    """True when the user prompt or planned tool batch includes a run step."""
    if _prompt_requests_run(user_prompt):
        return True
    for call in parsed_calls or []:
        if call.name == "run_cell":
            return True
    return False

BROWSER_WRITE_TOOLS = frozenset({
    "click_cell",
    "select_cell_by_index",
    "insert_cell",
    "edit_cell_by_index",
    "run_cell",
    "delete_by_index",
    "creating_markdown_by_index",
})

READ_TOOLS = frozenset({
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

TOOL_EXEC_ORDER: dict[str, int] = {
    "notebook_snapshot_status": 0,
    "notebook_list_cells": 0,
    "notebook_graph_query": 0,
    "notebook_search": 0,
    "notebook_overview": 0,
    "notebook_executed_cells": 0,
    "notebook_find_symbol": 0,
    "notebook_recommend_placement": 0,
    "notebook_get_cell": 0,
    "notebook_get_cells": 0,
    "notebook_cell_neighbors": 0,
    "select_cell_by_index": 10,
    "click_cell": 10,
    "insert_cell": 20,
    "creating_markdown_by_index": 25,
    "edit_cell_by_index": 30,
    "run_cell": 40,
    "delete_by_index": 50,
}


@dataclass
class ParsedToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


def _parse_tool_calls(
    tool_calls: list[dict],
    *,
    url: str,
    tab_id: int | None,
) -> list[ParsedToolCall]:
    parsed: list[ParsedToolCall] = []
    for idx, tc in enumerate(tool_calls):
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        name = str(fn.get("name") or "").strip()
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        except Exception:
            args = {}
        args.setdefault("url", url)
        if isinstance(tab_id, int) and tab_id > 0:
            args.setdefault("tab_id", tab_id)
        call_id = tc.get("id") if isinstance(tc, dict) else None
        if not call_id:
            call_id = f"call_{name}_{idx}"
        parsed.append(ParsedToolCall(id=str(call_id), name=name, args=args))
    return parsed


def _sort_tool_calls(calls: list[ParsedToolCall]) -> list[ParsedToolCall]:
    return [c for _, c in sorted(
        enumerate(calls),
        key=lambda pair: (TOOL_EXEC_ORDER.get(pair[1].name, 35), pair[0]),
    )]


def partition_batch(
    calls: list[ParsedToolCall],
) -> tuple[list[ParsedToolCall], list[ParsedToolCall]]:
    """
    One host batch = all pre-run tools, then every run_cell, in LLM emission order.
    Only tools listed *after* the first run_cell that are not run_cell are deferred.
    """
    execute_now: list[ParsedToolCall] = []
    deferred: list[ParsedToolCall] = []
    seen_run = False
    for call in calls:
        if call.name == "run_cell":
            execute_now.append(call)
            seen_run = True
        elif seen_run and call.name in (BROWSER_WRITE_TOOLS | READ_TOOLS):
            deferred.append(call)
        else:
            execute_now.append(call)
    return execute_now, deferred


def _prompt_requests_run(prompt: str) -> bool:
    text = str(prompt or "").lower()
    needles = (
        " and run",
        " then run",
        " run it",
        " run those",
        " execute those",
        " execute them",
        " execute it",
        " run the cell",
        " execute the cell",
        " and execute",
        " run cell",
        " execute cell",
        " and run it",
    )
    if any(n in text for n in needles):
        return True
    return bool(re.search(r"\b(run|execute)\b.*\b(cells?|these|them)\b", text))


split_batch_at_run = partition_batch


def expand_multi_cell_from_prompt(
    calls: list[ParsedToolCall],
    *,
    user_prompt: str,
    url: str,
    tab_id: int | None,
) -> list[ParsedToolCall]:
    """
    When the user asks for N new cells but the batch is incomplete, synthesize
    insert + edit (+ run) chains for every cell in one batch.
    """
    n_requested = extract_cell_count_from_prompt(user_prompt)
    if n_requested is None:
        return calls

    existing_inserts = [c for c in calls if c.name == "insert_cell"]
    existing_edits = [c for c in calls if c.name == "edit_cell_by_index"]
    if len(existing_inserts) >= n_requested and len(existing_edits) >= n_requested:
        return calls

    anchor = extract_insert_anchor_from_prompt(user_prompt)
    if anchor is None and existing_inserts:
        try:
            anchor = int(existing_inserts[0].args.get("index") or existing_inserts[0].args.get("cell_index"))
        except (TypeError, ValueError):
            anchor = None
    if anchor is None:
        return calls

    contents = parse_multi_cell_contents(user_prompt, n_requested)
    want_run = user_requests_run(user_prompt, calls)
    preserved = [
        c for c in calls
        if c.name not in {"insert_cell", "edit_cell_by_index", "run_cell", "creating_markdown_by_index"}
    ]
    out = list(preserved)

    for i in range(n_requested):
        insert_anchor = anchor + i
        cell_index = anchor + 1 + i
        ins_args: dict[str, Any] = {
            "index": insert_anchor,
            "direction": "below",
            "url": url,
        }
        edit_args: dict[str, Any] = {
            "cell_index": cell_index,
            "content": contents[i],
            "url": url,
        }
        if isinstance(tab_id, int) and tab_id > 0:
            ins_args["tab_id"] = tab_id
            edit_args["tab_id"] = tab_id
        out.append(ParsedToolCall(id=f"host_multi_ins_{i}", name="insert_cell", args=ins_args))
        out.append(ParsedToolCall(id=f"host_multi_edit_{i}", name="edit_cell_by_index", args=edit_args))
        if want_run:
            run_args: dict[str, Any] = {"cell_index": cell_index, "url": url}
            if isinstance(tab_id, int) and tab_id > 0:
                run_args["tab_id"] = tab_id
            out.append(ParsedToolCall(id=f"host_multi_run_{i}", name="run_cell", args=run_args))

    return out


def fetch_queue_cell_evidence(
    registry,
    url: str,
    cell_indices: list[int] | int,
) -> dict[str, Any]:
    """Fetch input + output for every target cell after the tool queue finishes."""
    if isinstance(cell_indices, int):
        indices = [cell_indices]
    else:
        indices = sorted({int(i) for i in cell_indices if i is not None})

    cells: list[dict[str, Any]] = []
    for cell_index in indices:
        try:
            raw = registry.call(
                "notebook_get_cell",
                {"url": url, "cell_index": cell_index, "include_output": True},
            )
        except Exception as exc:
            raw = {"ok": False, "cell_index": cell_index, "error": str(exc)}
        if not isinstance(raw, dict):
            raw = {"ok": False, "cell_index": cell_index, "error": "invalid cell payload"}
        cells.append(
            {
                "cell_index": cell_index,
                "ok": raw.get("ok", True),
                "type": raw.get("type"),
                "input": raw.get("input") or raw.get("source") or raw.get("content"),
                "output": raw.get("output"),
                "execution_order": raw.get("execution_order"),
                "error": raw.get("error"),
            }
        )

    return {
        "cell_indices": indices,
        "cells": cells,
        "count": len(cells),
    }


fetch_post_run_cell_evidence = fetch_queue_cell_evidence


def _target_cell_indices(
    *,
    expected_edits: dict[int, str],
    run_requested: list[int],
    run_completed: list[int],
) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for ci in list(expected_edits.keys()) + run_requested + run_completed:
        try:
            idx = int(ci)
        except (TypeError, ValueError):
            continue
        if idx not in seen:
            seen.add(idx)
            ordered.append(idx)
    return ordered


def finalize_tool_queue_verification(
    verification: dict[str, Any],
    *,
    registry,
    url: str,
    expected_edits: dict[int, str],
    run_requested: list[int],
    run_completed: list[int],
    run_pending: list[int],
    user_prompt: str = "",
    run_waits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Close or reopen the ReAct loop after the host tool queue finishes.
    Success → all target cells with input+output for LLM verification once.
    Error   → stop queue, return error context for LLM fix turn.
    """
    targets = _target_cell_indices(
        expected_edits=expected_edits,
        run_requested=run_requested,
        run_completed=run_completed,
    )
    if targets and registry is not None:
        evidence = fetch_queue_cell_evidence(registry, url, targets)
        verification["queue_cell_evidence"] = evidence
        verification["target_cells"] = evidence.get("cells") or []
        verification["post_run_query"] = evidence

    had_error = bool(verification.get("needs_fix") or verification.get("execution_error"))
    runs_planned = bool(run_requested)
    all_runs_done = runs_planned and not run_pending and len(run_completed) == len(run_requested)
    writes_only_ok = not runs_planned and bool(expected_edits) and verification.get("verified")
    workflow_verified = verification.get("verified") is not False

    verification["tool_queue"] = {
        "run_requested": run_requested,
        "run_completed": run_completed,
        "run_pending": run_pending,
        "delay_sec": INTER_TOOL_DELAY_SEC,
    }

    if had_error:
        verification["tool_queue_status"] = "error"
        verification["tool_queue_stopped"] = True
        verification["run_queue_stopped"] = True
        verification["pending_run_cells"] = run_pending
        err = verification.get("execution_error") or {}
        failed_ci = err.get("cell_index") or (run_completed[-1] if run_completed else None)
        verification["await_llm_summary"] = False
        verification["close_react_loop"] = False
        failed_evidence = None
        if failed_ci is not None and registry is not None:
            failed_evidence = fetch_queue_cell_evidence(registry, url, int(failed_ci))
        verification["error_recovery"] = {
            "original_user_task": str(user_prompt or "").strip(),
            "failed_cell_index": failed_ci,
            "run_completed_before_stop": list(run_completed),
            "pending_run_cells": list(run_pending),
            "may_propagate": bool(run_pending),
            "failed_cell_evidence": failed_evidence,
            "execution_error": err,
        }
        propagation = (
            f"Error may affect downstream cells {run_pending} — fix cell {failed_ci} "
            "then re-run failed + pending cells if needed."
            if run_pending
            else f"Error isolated to cell {failed_ci} — fix and re-run that cell, then finish the user task."
        )
        verification["user_response_gate"] = (
            f"Tool queue stopped at cell {failed_ci}. Pending runs: {run_pending}. "
            f"{propagation} "
            "Inspect queue_cell_evidence / notebook read tools, fix with edit_cell_by_index, "
            "then emit ALL tool calls (including every run_cell) in one turn."
        )
    elif (all_runs_done or writes_only_ok) and workflow_verified and not had_error:
        verification["tool_queue_status"] = "complete"
        verification["tool_queue_complete"] = True
        verification["run_queue_complete"] = True
        verification["runs_requested"] = run_requested
        verification["runs_executed"] = run_completed
        verification["await_llm_summary"] = True
        verification["close_react_loop"] = True
        verification["user_response_gate"] = (
            "Tool queue complete. Verify every target cell in queue_cell_evidence "
            "(input + output). Write a final Summary — no further tools."
        )
    elif (all_runs_done or writes_only_ok) and not workflow_verified:
        verification["tool_queue_status"] = "verification_failed"
        verification["await_llm_summary"] = False
        verification["close_react_loop"] = False
        verification["needs_fix"] = True
        verification["user_response_gate"] = (
            "Tool dispatch finished but workflow verification failed — inspect queue_cell_evidence "
            "and emit fixes via tools. Do not claim success."
        )
    elif run_pending:
        verification["tool_queue_status"] = "incomplete"
        verification["runs_incomplete"] = True
        verification["pending_run_cells"] = run_pending

    try:
        from .agent_goal_verification import apply_goal_verification_layer
    except Exception:
        from agent_goal_verification import apply_goal_verification_layer
    verification = apply_goal_verification_layer(
        verification,
        user_prompt=user_prompt,
        expected_edits=expected_edits,
        run_waits=run_waits,
        run_indices=run_completed,
    )

    return verification


def enrich_batch_from_prompt(
    calls: list[ParsedToolCall],
    *,
    user_prompt: str,
    url: str,
    tab_id: int | None,
    registry=None,
) -> list[ParsedToolCall]:
    """Add missing edit/run steps when the user prompt implies them."""
    out = expand_multi_cell_from_prompt(
        calls,
        user_prompt=user_prompt,
        url=url,
        tab_id=tab_id,
    )
    if extract_cell_count_from_prompt(user_prompt) is not None:
        return _sort_tool_calls(out)

    names = {c.name for c in out}
    try:
        from .agentic_action_guard import is_write_only_request
    except Exception:
        from agentic_action_guard import is_write_only_request
    write_only = is_write_only_request(user_prompt)

    if "insert_cell" in names and "edit_cell_by_index" not in names:
        insert_call = next(c for c in out if c.name == "insert_cell")
        chain_args = build_edit_after_insert(
            user_prompt,
            insert_call.args,
            {"ok": True, "dispatched": True, "phase": "dispatched"},
            url=url,
            tab_id=tab_id,
        )
        if chain_args:
            out.append(
                ParsedToolCall(
                    id="host_enrich_edit",
                    name="edit_cell_by_index",
                    args=chain_args,
                )
            )
            names.add("edit_cell_by_index")

    if user_requests_run(user_prompt, out) and "run_cell" not in names and not write_only:
        edit_map = {
            int(c.args["cell_index"]): str(c.args.get("content") or "")
            for c in out
            if c.name == "edit_cell_by_index" and c.args.get("cell_index") is not None
        }
        try:
            from .agentic_pipeline import is_sequential_pipeline
        except Exception:
            from agentic_pipeline import is_sequential_pipeline
        if len(edit_map) >= 2 and is_sequential_pipeline(user_prompt, edit_map):
            return _sort_tool_calls(out)

        run_index = None
        for call in reversed(out):
            if call.name == "edit_cell_by_index":
                run_index = call.args.get("cell_index")
                break
            if call.name == "insert_cell":
                run_index = infer_new_cell_index(call.args, {"ok": True})
                break
        if run_index is not None:
            run_args: dict[str, Any] = {"cell_index": int(run_index), "url": url}
            if isinstance(tab_id, int) and tab_id > 0:
                run_args["tab_id"] = tab_id
            out.append(ParsedToolCall(id="host_enrich_run", name="run_cell", args=run_args))

    return _sort_tool_calls(out)


def _run_wait_failed(wait: dict[str, Any]) -> bool:
    if wait.get("pending"):
        return True
    if not wait.get("ok"):
        return True
    if wait.get("has_error"):
        return True
    if wait.get("run_succeeded") is False:
        return True
    output = str(wait.get("output") or "").strip()
    if wait.get("run_succeeded") is True and output:
        return False
    if wait.get("run_completed") and wait.get("run_succeeded") is True:
        return False
    if wait.get("run_cell_result"):
        rc = wait["run_cell_result"]
        if rc.get("success"):
            return False
        if rc.get("pending"):
            return True
        if rc.get("finished") and not rc.get("success"):
            return True
    if not wait.get("run_completed") and wait.get("ok"):
        if not output:
            return True
        analysis = analyze_cell_output(output)
        if analysis.get("pending"):
            return True
        if analysis.get("has_error"):
            return True
        return not analysis.get("run_succeeded", False)
    if wait.get("run_succeeded") is None:
        analysis = analyze_cell_output(wait.get("output"))
        if analysis.get("pending"):
            return True
        return not analysis.get("run_succeeded", False)
    return False


def _ordered_run_indices(
    run_calls: list[ParsedToolCall],
    wanted: list[int] | None = None,
) -> list[int]:
    """Preserve LLM emission order; append any host-resolved cells not yet listed."""
    seen: set[int] = set()
    ordered: list[int] = []
    for call in run_calls:
        try:
            ci = int(call.args.get("cell_index"))
        except (TypeError, ValueError):
            continue
        if ci not in seen:
            seen.add(ci)
            ordered.append(ci)
    for ci in wanted or []:
        if ci not in seen:
            seen.add(ci)
            ordered.append(ci)
    return ordered


def _poll_sleep(seconds: float, cancel_check=None) -> bool:
    """Sleep up to seconds; return True if cancel_check fired."""
    if not callable(cancel_check):
        time.sleep(max(0.0, float(seconds)))
        return False
    try:
        from .streaming import _interruptible_sleep
    except Exception:
        try:
            from streaming import _interruptible_sleep
        except Exception:
            _interruptible_sleep = None
    if _interruptible_sleep is not None:
        return bool(_interruptible_sleep(seconds, cancel_check))
    end = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < end:
        if cancel_check():
            return True
        time.sleep(min(0.1, end - time.monotonic()))
    return bool(cancel_check())


def execute_run_queue_sequential(
    run_indices: list[int],
    *,
    executed: list[dict[str, Any]],
    registry,
    url: str,
    tab_id: int | None,
    mode: str,
    browser_tool_allowed: Callable[[str | None, str], tuple[bool, str | None]],
    inter_delay: float = INTER_TOOL_DELAY_SEC,
    stop_on_error: bool = True,
    cancel_check=None,
) -> tuple[list[int], list[dict[str, Any]], list[int]]:
    """
    Run cells one-by-one: dispatch → wait → check. Stop queue on first error.
    Returns (completed_indices, run_waits, pending_indices).
    """
    if not run_indices:
        return [], [], []

    completed: list[int] = []
    waits: list[dict[str, Any]] = []
    snap_for_run, _ = load_notebook_snapshot(url)

    for run_idx, run_ci in enumerate(run_indices):
        if callable(cancel_check) and cancel_check():
            return completed, waits, run_indices[run_idx:]
        if run_idx > 0:
            if _poll_sleep(inter_delay, cancel_check):
                return completed, waits, run_indices[run_idx:]

        dispatch = _dispatch_run_cell(
            registry,
            url=url,
            tab_id=tab_id,
            cell_index=int(run_ci),
            mode=mode,
            browser_tool_allowed=browser_tool_allowed,
        )
        if not dispatch.get("ok"):
            waits.append(
                {
                    "ok": False,
                    "error": dispatch.get("error") or "dispatch failed",
                    "cell_index": run_ci,
                    "run_succeeded": False,
                }
            )
            executed.append(
                {
                    "tool": "run_cell",
                    "dispatched": False,
                    "phase": "dispatch_failed",
                    "cell_index": int(run_ci),
                }
            )
            return completed, waits, run_indices[run_idx:]

        if _poll_sleep(POST_BATCH_SETTLE_SEC, cancel_check):
            return completed, waits, run_indices[run_idx:]
        snap_for_run, _ = load_notebook_snapshot(url)
        started_at = time.monotonic()
        wait = wait_for_cell_run(url, int(run_ci), snap_for_run, cancel_check=cancel_check)
        if wait.get("run_succeeded") is None and wait.get("output"):
            analysis = analyze_cell_output(wait.get("output"))
            for key in ("run_succeeded", "has_error", "error_type", "error_summary", "output_preview"):
                if wait.get(key) is None and analysis.get(key) is not None:
                    wait[key] = analysis[key]
        run_result = build_run_cell_result(int(run_ci), wait, started_at=started_at)
        wait["run_cell_result"] = run_result.to_dict()
        wait["pending"] = run_result.pending
        wait["run_completed"] = run_result.finished
        wait["run_succeeded"] = run_result.success if run_result.finished else False
        if wait.get("cancelled"):
            return completed, waits, run_indices[run_idx:]
        waits.append(wait)
        completed.append(int(run_ci))
        executed.append(
            {
                "tool": "run_cell",
                "dispatched": bool(wait.get("ok")),
                "phase": "run_queue",
                "cell_index": int(run_ci),
            }
        )
        snap_for_run, _ = load_notebook_snapshot(url)

        if stop_on_error and _run_wait_failed(wait):
            return completed, waits, run_indices[run_idx + 1:]

    return completed, waits, []


def _attach_run_queue_verification(
    verification: dict[str, Any],
    *,
    requested: list[int],
    completed: list[int],
    pending: list[int],
    registry=None,
    url: str = "",
    expected_edits: dict[int, str] | None = None,
) -> dict[str, Any]:
    return finalize_tool_queue_verification(
        verification,
        registry=registry,
        url=url,
        expected_edits=expected_edits or {},
        run_requested=requested,
        run_completed=completed,
        run_pending=pending,
    )


def enrich_run_cells_from_prompt(
    calls: list[ParsedToolCall],
    *,
    user_prompt: str,
    url: str,
    registry,
    tab_id: int | None = None,
) -> list[ParsedToolCall]:
    """Expand run_cell queue when user asked to run last N code cells."""
    try:
        from .agentic_action_guard import resolve_wanted_run_cells, user_requests_run
    except Exception:
        from agentic_action_guard import resolve_wanted_run_cells, user_requests_run

    if not user_requests_run(user_prompt) or registry is None:
        return calls

    wanted = resolve_wanted_run_cells(user_prompt, calls, registry=registry, url=url)
    if len(wanted) < 2:
        return calls

    existing = {
        int(c.args["cell_index"])
        for c in calls
        if c.name == "run_cell" and c.args.get("cell_index") is not None
    }
    if existing == set(wanted):
        return calls

    out = [c for c in calls if c.name != "run_cell"]
    for i, ci in enumerate(wanted):
        run_args: dict[str, Any] = {"cell_index": int(ci), "url": url}
        if isinstance(tab_id, int) and tab_id > 0:
            run_args["tab_id"] = tab_id
        out.append(
            ParsedToolCall(
                id=f"host_enrich_run_{i}",
                name="run_cell",
                args=run_args,
            )
        )
    return _sort_tool_calls(out)


def normalize_sequential_insert_anchors(calls: list[ParsedToolCall]) -> list[ParsedToolCall]:
    """Chain insert below M → below M+1 → … when multiple inserts share one anchor region."""
    inserts = [c for c in calls if c.name == "insert_cell"]
    if len(inserts) <= 1:
        return calls

    first = inserts[0]
    direction = str(first.args.get("direction") or "below").lower()
    if direction != "below":
        return calls

    try:
        start_anchor = int(first.args.get("index") or first.args.get("cell_index"))
    except (TypeError, ValueError):
        return calls

    normalized: list[ParsedToolCall] = []
    insert_idx = 0
    for call in calls:
        if call.name != "insert_cell":
            normalized.append(call)
            continue
        args = dict(call.args)
        args["index"] = start_anchor + insert_idx
        args["direction"] = "below"
        normalized.append(ParsedToolCall(id=call.id, name=call.name, args=args))
        insert_idx += 1
    return normalized


def _insert_new_cell_indices(inserts: list[ParsedToolCall]) -> list[int]:
    """Deterministic 1-based indices for cells created by sequential below-inserts."""
    if not inserts:
        return []
    try:
        start_anchor = int(inserts[0].args.get("index") or inserts[0].args.get("cell_index"))
    except (TypeError, ValueError):
        return []
    direction = str(inserts[0].args.get("direction") or "below").strip().lower()
    if direction != "below":
        return [start_anchor + i for i in range(len(inserts))]
    return [start_anchor + 1 + i for i in range(len(inserts))]


def normalize_batch_indices(calls: list[ParsedToolCall]) -> list[ParsedToolCall]:
    """Align edit/run indices for single- or multi-insert batches."""
    calls = normalize_sequential_insert_anchors(calls)
    inserts = [c for c in calls if c.name == "insert_cell"]
    if not inserts:
        return calls

    new_indices = _insert_new_cell_indices(inserts)
    if not new_indices:
        return calls

    try:
        anchor = int(inserts[0].args.get("index") or inserts[0].args.get("cell_index"))
    except (TypeError, ValueError):
        anchor = new_indices[0] - 1

    edit_calls = [c for c in calls if c.name == "edit_cell_by_index"]
    run_calls = [c for c in calls if c.name == "run_cell"]
    edit_targets = {id(c): new_indices[min(i, len(new_indices) - 1)] for i, c in enumerate(edit_calls)}
    run_targets = {id(c): new_indices[min(i, len(new_indices) - 1)] for i, c in enumerate(run_calls)}

    normalized: list[ParsedToolCall] = []
    for call in calls:
        if call.name == "edit_cell_by_index":
            args = dict(call.args)
            try:
                ci = int(args.get("cell_index"))
            except (TypeError, ValueError):
                ci = None
            if ci is None or ci <= anchor + len(inserts) - 1 or ci in {anchor} | set(new_indices):
                args["cell_index"] = edit_targets.get(id(call), new_indices[0])
            normalized.append(ParsedToolCall(id=call.id, name=call.name, args=args))
        elif call.name == "run_cell":
            args = dict(call.args)
            try:
                ci = int(args.get("cell_index"))
            except (TypeError, ValueError):
                ci = None
            if ci is None or ci <= anchor + len(inserts) - 1 or ci in {anchor} | set(new_indices):
                args["cell_index"] = run_targets.get(id(call), new_indices[0])
            normalized.append(ParsedToolCall(id=call.id, name=call.name, args=args))
        else:
            normalized.append(call)
    return normalized


def should_use_batch_executor(tool_calls: list[dict], *, agentic_active: bool) -> bool:
    if not agentic_active or not tool_calls:
        return False
    parsed = _parse_tool_calls(tool_calls, url="", tab_id=None)
    if any(c.name in BROWSER_WRITE_TOOLS for c in parsed):
        return True
    return any(c.name in READ_TOOLS for c in parsed) and any(
        c.name == "run_cell" for c in parsed
    )


def _cell_by_index(data: dict | None, cell_index: int) -> dict | None:
    if not isinstance(data, dict):
        return None
    for cell in data.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        try:
            if int(cell.get("index", -1)) == int(cell_index):
                return cell
        except (TypeError, ValueError):
            continue
    return None


def wait_for_snapshot_change(
    url: str,
    before_fingerprint: str,
    *,
    timeout: float = SNAPSHOT_CHANGE_TIMEOUT_SEC,
    poll_interval: float = SNAPSHOT_POLL_INTERVAL_SEC,
) -> tuple[dict | None, str]:
    deadline = time.monotonic() + max(0.5, float(timeout))
    last_data, last_source = load_notebook_snapshot(url)
    while time.monotonic() < deadline:
        data, source = load_notebook_snapshot(url)
        last_data, last_source = data, source
        cells = cells_from_snapshot(data)
        if cells and snapshot_fingerprint(cells) != before_fingerprint:
            return data, source
        time.sleep(poll_interval)
    return last_data, last_source


def wait_for_cell_run(
    url: str,
    cell_index: int,
    before_data: dict | None,
    *,
    timeout: float = RUN_WAIT_TIMEOUT_SEC,
    poll_interval: float = RUN_POLL_INTERVAL_SEC,
    cancel_check=None,
) -> dict[str, Any]:
    before_cell = _cell_by_index(before_data, cell_index)
    before_order = before_cell.get("execution_order") if before_cell else None
    before_output = str((before_cell or {}).get("output") or "")

    deadline = time.monotonic() + max(1.0, float(timeout))
    last_error = "timeout waiting for cell execution"

    while time.monotonic() < deadline:
        if callable(cancel_check) and cancel_check():
            return {"ok": False, "cancelled": True, "error": "stopped by user", "cell_index": cell_index}
        data, source = load_notebook_snapshot(url)
        cell = _cell_by_index(data, cell_index)
        if not cell:
            if _poll_sleep(poll_interval, cancel_check):
                return {"ok": False, "cancelled": True, "error": "stopped by user", "cell_index": cell_index}
            continue

        order = cell.get("execution_order")
        output = str(cell.get("output") or "")
        order_changed = order is not None and order != before_order
        output_changed = bool(output) and output != before_output

        if order_changed or output_changed:
            if order_changed and not output.strip():
                if _poll_sleep(poll_interval, cancel_check):
                    return {"ok": False, "cancelled": True, "error": "stopped by user", "cell_index": cell_index}
                continue
            analysis = analyze_cell_output(output)
            finished = bool(output.strip()) or bool(analysis.get("has_error"))
            return {
                "ok": True,
                "started": True,
                "run_completed": finished,
                "pending": not finished and not analysis.get("has_error"),
                "run_succeeded": analysis.get("run_succeeded") if finished else False,
                "cell_index": cell_index,
                "execution_order": order,
                "execution_title": cell.get("execution_title"),
                "output": output[:MAX_CELL_OUTPUT_CHARS],
                "snapshot": source,
                "wait_reason": "execution_order_changed" if order_changed else "output_changed",
                **analysis,
            }

        if _poll_sleep(poll_interval, cancel_check):
            return {"ok": False, "cancelled": True, "error": "stopped by user", "cell_index": cell_index}

    return {"ok": False, "error": last_error, "cell_index": cell_index}


def _dispatch_run_cell(
    registry,
    *,
    url: str,
    tab_id: int | None,
    cell_index: int,
    mode: str,
    browser_tool_allowed: Callable[[str | None, str], tuple[bool, str | None]],
) -> dict[str, Any]:
    args: dict[str, Any] = {"cell_index": int(cell_index), "url": url}
    if isinstance(tab_id, int) and tab_id > 0:
        args["tab_id"] = tab_id
    allowed, block_err = browser_tool_allowed(mode, "run_cell")
    if not allowed:
        return {"ok": False, "error": block_err, "tool": "run_cell"}
    try:
        return dict(registry.call("run_cell", args) or {})
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tool": "run_cell"}


def _content_matches(expected: str, actual: str) -> bool:
    exp = str(expected or "").replace("\r\n", "\n").strip()
    act = str(actual or "").replace("\r\n", "\n").strip()
    if not exp:
        return True
    if act == exp:
        return True
    return exp in act


def verify_workflow_batch(
    *,
    before_data: dict | None,
    after_data: dict | None,
    executed: list[dict[str, Any]],
    expected_edits: dict[int, str],
    run_cell_indices: list[int] | None = None,
    run_waits: list[dict[str, Any]] | None = None,
    run_cell_index: int | None = None,
    run_wait: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if run_cell_indices is None:
        run_cell_indices = [run_cell_index] if run_cell_index is not None else []
    if run_waits is None:
        run_waits = [run_wait] if run_wait is not None else []

    before_cells = cells_from_snapshot(before_data)
    after_cells = cells_from_snapshot(after_data)
    batch_checks: list[dict[str, Any]] = []
    verified = True

    tool_names = [row.get("tool") for row in executed]

    insert_count = sum(1 for name in tool_names if name == "insert_cell")
    if insert_count:
        ok = len(after_cells) >= len(before_cells) + insert_count
        batch_checks.append(
            {
                "tool": "insert_cell",
                "ok": ok,
                "expected_new_cells": insert_count,
                "cell_count_before": len(before_cells),
                "cell_count_after": len(after_cells),
            }
        )
        verified = verified and ok

    if "delete_by_index" in tool_names:
        ok = len(after_cells) < len(before_cells)
        batch_checks.append(
            {
                "tool": "delete_by_index",
                "ok": ok,
                "cell_count_before": len(before_cells),
                "cell_count_after": len(after_cells),
            }
        )
        verified = verified and ok

    for cell_index, expected_content in expected_edits.items():
        cell = _cell_by_index(after_data, cell_index)
        actual = str((cell or {}).get("input") or "")
        match = _content_matches(expected_content, actual)
        batch_checks.append(
            {
                "tool": "edit_cell_by_index",
                "ok": match,
                "cell_index": cell_index,
                "content_match": match,
            }
        )
        verified = verified and match

    cell_output = None
    run_completed = False
    run_succeeded: bool | None = None
    execution_error: dict[str, Any] | None = None
    run_results: list[dict[str, Any]] = []

    for idx, run_ci in enumerate(run_cell_indices):
        run_wait_item = run_waits[idx] if idx < len(run_waits) else None
        if run_ci is None:
            continue

        if run_wait_item and run_wait_item.get("ok"):
            run_completed = True
            output = run_wait_item.get("output")
            succeeded = run_wait_item.get("run_succeeded")
            if succeeded is None:
                analysis = analyze_cell_output(output)
                succeeded = analysis.get("run_succeeded")
            run_entry: dict[str, Any] = {
                "tool": "run_cell",
                "ok": bool(succeeded),
                "run_completed": True,
                "run_succeeded": succeeded,
                "cell_index": run_ci,
                "execution_order": run_wait_item.get("execution_order"),
                "wait_reason": run_wait_item.get("wait_reason"),
                "output_preview": (output or "")[:500],
            }
            if run_wait_item.get("has_error") or succeeded is False:
                err = {
                    "cell_index": run_ci,
                    "error_type": run_wait_item.get("error_type"),
                    "error_summary": run_wait_item.get("error_summary"),
                    "cell_output": output,
                    "output_preview": run_wait_item.get("output_preview"),
                }
                run_entry["execution_error"] = err
                if execution_error is None:
                    execution_error = err
                verified = False
            batch_checks.append(run_entry)
            run_results.append({**run_entry, "output": output})
            if cell_output is None:
                cell_output = output
            elif output:
                cell_output = f"{cell_output}\n---\n{output}"
            if run_succeeded is None:
                run_succeeded = succeeded
            elif succeeded is False:
                run_succeeded = False
        else:
            verified = False
            batch_checks.append(
                {
                    "tool": "run_cell",
                    "ok": False,
                    "run_completed": False,
                    "run_succeeded": False,
                    "cell_index": run_ci,
                    "error": (run_wait_item or {}).get("error") or "run not verified",
                }
            )
            run_succeeded = False

    if run_cell_indices and cell_output is None:
        outputs = []
        for run_ci in run_cell_indices:
            cell = _cell_by_index(after_data, run_ci)
            if cell:
                outputs.append(str(cell.get("output") or ""))
        if outputs:
            cell_output = "\n---\n".join(o for o in outputs if o)[:MAX_CELL_OUTPUT_CHARS]
            if run_completed and run_succeeded is None:
                analysis = analyze_cell_output(cell_output)
                run_succeeded = analysis.get("run_succeeded")
                if analysis.get("has_error"):
                    verified = False
                    execution_error = execution_error or {
                        "cell_index": run_cell_indices[0],
                        "error_type": analysis.get("error_type"),
                        "error_summary": analysis.get("error_summary"),
                        "cell_output": cell_output,
                        "output_preview": analysis.get("output_preview"),
                    }

    phase = "verified"
    if execution_error:
        phase = "execution_error"
    elif not verified:
        phase = "verification_failed"

    return {
        "ok": verified,
        "verified": verified,
        "workflow_verification": True,
        "run_completed": run_completed,
        "run_succeeded": run_succeeded,
        "execution_error": execution_error,
        "batch": batch_checks,
        "executed": executed,
        "executed_tools": tool_names,
        "cell_index": run_cell_indices[-1] if run_cell_indices else None,
        "cell_indices": run_cell_indices,
        "run_results": run_results,
        "cell_output": cell_output,
        "phase": phase,
        "needs_fix": execution_error is not None,
    }


def _collect_deferred_run_calls(deferred_calls: list[ParsedToolCall]) -> list[ParsedToolCall]:
    return [c for c in deferred_calls if c.name == "run_cell"]


def _non_run_deferred(deferred_calls: list[ParsedToolCall]) -> list[ParsedToolCall]:
    return [c for c in deferred_calls if c.name != "run_cell"]


def execute_agentic_batch(
    tool_calls: list[dict],
    *,
    user_prompt: str,
    url: str,
    tab_id: int | None,
    registry,
    browser_tool_allowed: Callable[[str | None, str], tuple[bool, str | None]],
    mode: str,
    inter_delay: float = INTER_TOOL_DELAY_SEC,
    pipeline_state: dict[str, Any] | None = None,
    cancel_check=None,
) -> dict[str, Any]:
    try:
        from .agentic_pipeline import (
            attach_pipeline_to_verification,
            init_pipeline_state,
            is_sequential_pipeline,
            record_pipeline_run,
            strip_runs_for_write_phase,
        )
    except Exception:
        from agentic_pipeline import (
            attach_pipeline_to_verification,
            init_pipeline_state,
            is_sequential_pipeline,
            record_pipeline_run,
            strip_runs_for_write_phase,
        )

    before_data, _before_source = load_notebook_snapshot(url)
    before_cells = cells_from_snapshot(before_data)
    before_fp = snapshot_fingerprint(before_cells) if before_cells else ""

    queue_state = build_execution_queue(tool_calls)
    run_waits: list[dict[str, Any]] = []

    def _strict_return(verification: dict[str, Any]) -> dict[str, Any]:
        for wait in run_waits:
            ci = wait.get("cell_index")
            if ci is not None:
                queue_state.run_results.append(build_run_cell_result(int(ci), wait))
        return attach_strict_execution(
            verification,
            queue_state=queue_state,
            user_prompt=user_prompt,
            executor_called=True,
        )

    parsed = _parse_tool_calls(tool_calls, url=url, tab_id=tab_id)
    parsed = enrich_batch_from_prompt(
        parsed,
        user_prompt=user_prompt,
        url=url,
        tab_id=tab_id,
        registry=registry,
    )
    parsed = enrich_run_cells_from_prompt(
        parsed,
        user_prompt=user_prompt,
        url=url,
        registry=registry,
        tab_id=tab_id,
    )
    try:
        from .agentic_action_guard import (
            is_write_only_request,
            is_run_verify_request,
            resolve_wanted_run_cells,
            user_requests_run,
        )
    except Exception:
        from agentic_action_guard import (
            is_write_only_request,
            is_run_verify_request,
            resolve_wanted_run_cells,
            user_requests_run,
        )
    wanted_run_cells = resolve_wanted_run_cells(
        user_prompt, parsed, registry=registry, url=url
    )
    write_only = is_write_only_request(user_prompt)
    run_verify = is_run_verify_request(user_prompt)
    if write_only:
        parsed = [c for c in parsed if c.name != "run_cell"]

    parsed = normalize_batch_indices(parsed)

    pre_expected_edits: dict[int, str] = {}
    for call in parsed:
        if call.name == "edit_cell_by_index":
            try:
                ci = int(call.args.get("cell_index"))
                content = call.args.get("content")
                if content is not None:
                    pre_expected_edits[ci] = str(content)
            except (TypeError, ValueError):
                pass

    run_calls_pre = [c for c in parsed if c.name == "run_cell"]
    run_only_batch = bool(run_calls_pre) and not pre_expected_edits and not any(
        c.name in {"insert_cell", "edit_cell_by_index", "delete_by_index", "creating_markdown_by_index"}
        for c in parsed
    )

    full_write_run_batch = bool(pre_expected_edits) and bool(run_calls_pre)

    sequential_pipeline = (
        not write_only
        and not run_verify
        and not run_only_batch
        and not (EXECUTE_FULL_BATCH_WITHOUT_LLM_SPLIT and full_write_run_batch)
        and not run_calls_pre
        and (
            bool(pipeline_state and pipeline_state.get("active"))
            or is_sequential_pipeline(user_prompt, pre_expected_edits)
        )
    )

    if (
        EXECUTE_FULL_BATCH_WITHOUT_LLM_SPLIT
        and full_write_run_batch
    ):
        deferred_from_strip = []
    elif sequential_pipeline and pre_expected_edits and run_calls_pre and not user_requests_run(user_prompt):
        stripped, extra_runs = strip_runs_for_write_phase(parsed, sequential=True)
        parsed = stripped
        deferred_from_strip = extra_runs
    else:
        deferred_from_strip = []

    parsed, deferred_calls = partition_batch(parsed)

    deferred_run_calls = _collect_deferred_run_calls(deferred_calls)
    deferred_calls = _non_run_deferred(deferred_calls)

    if run_only_batch or (user_requests_run(user_prompt) and run_calls_pre):
        pass  # never split a run-only / run-request batch across LLM rounds
    elif EXECUTE_FULL_BATCH_WITHOUT_LLM_SPLIT and full_write_run_batch:
        pass  # execute insert/edit/run in one host batch
    elif sequential_pipeline and pipeline_state and pipeline_state.get("active"):
        run_calls = [c for c in parsed if c.name == "run_cell"]
        if len(run_calls) > 1:
            deferred_calls = deferred_calls + run_calls[1:]
            parsed = [c for c in parsed if c not in run_calls[1:]]
            deferred_run_calls = _collect_deferred_run_calls(deferred_calls)
            deferred_calls = _non_run_deferred(deferred_calls)

    parsed = _sort_tool_calls(parsed)
    if deferred_from_strip:
        deferred_calls = deferred_from_strip + deferred_calls
        deferred_run_calls = deferred_run_calls + _collect_deferred_run_calls(deferred_from_strip)
        deferred_calls = _non_run_deferred(deferred_calls)

    executed: list[dict[str, Any]] = []
    read_results: list[dict[str, Any]] = []
    expected_edits: dict[int, str] = {}
    run_queue_calls: list[ParsedToolCall] = []
    wrote_browser = False
    prev_was_write = False
    pipeline = dict(pipeline_state) if isinstance(pipeline_state, dict) else None

    for call in parsed:
        if callable(cancel_check) and cancel_check():
            return _strict_return({
                "ok": False,
                "verified": False,
                "cancelled": True,
                "batch_executed": True,
                "executed": executed,
                "phase": "cancelled",
            })
        if call.name == "run_cell":
            run_queue_calls.append(call)
            continue

        if call.name in READ_TOOLS:
            try:
                allowed, block_err = browser_tool_allowed(mode, call.name)
                if not allowed:
                    result = {"ok": False, "error": block_err, "tool": call.name}
                else:
                    result = registry.call(call.name, call.args)
            except Exception as exc:
                result = {"ok": False, "error": str(exc), "tool": call.name}
            read_results.append({"tool": call.name, "result": result})
            executed.append(
                {
                    "tool": call.name,
                    "dispatched": bool((result or {}).get("ok")),
                    "phase": (result or {}).get("phase") or "read",
                }
            )
            continue

        if call.name not in BROWSER_WRITE_TOOLS:
            executed.append({"tool": call.name, "ok": False, "error": "unsupported tool in batch"})
            continue

        if prev_was_write:
            if _poll_sleep(inter_delay, cancel_check):
                break
        elif wrote_browser:
            if _poll_sleep(inter_delay, cancel_check):
                break

        try:
            allowed, block_err = browser_tool_allowed(mode, call.name)
            if not allowed:
                result = {"ok": False, "error": block_err, "tool": call.name}
            else:
                result = registry.call(call.name, call.args)
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "tool": call.name}

        result = dict(result or {})
        dispatched = bool(result.get("ok"))
        executed.append(
            {
                "tool": call.name,
                "dispatched": dispatched,
                "phase": result.get("phase") or ("dispatched" if dispatched else "dispatch_failed"),
                "cell_index": result.get("cell_index") or call.args.get("cell_index") or call.args.get("index"),
            }
        )
        ci_prog = result.get("cell_index") or call.args.get("cell_index") or call.args.get("index")
        try:
            ci_prog = int(ci_prog) if ci_prog is not None else None
        except (TypeError, ValueError):
            ci_prog = None
        record_queue_progress(
            queue_state,
            tool=call.name,
            dispatched=dispatched,
            cell_index=ci_prog,
            error=str(result.get("error") or "") if not dispatched else None,
        )

        if call.name == "edit_cell_by_index":
            try:
                ci = int(call.args.get("cell_index"))
                content = call.args.get("content")
                if content is not None:
                    expected_edits[ci] = str(content)
            except (TypeError, ValueError):
                pass

        wrote_browser = True
        prev_was_write = True

        if call.name == "insert_cell":
            if _poll_sleep(max(0.0, INSERT_SETTLE_SEC - inter_delay), cancel_check):
                break

    if callable(cancel_check) and cancel_check():
        return _strict_return({
            "ok": False,
            "verified": False,
            "cancelled": True,
            "batch_executed": True,
            "executed": executed,
            "phase": "cancelled",
        })

    run_queue_calls.extend(deferred_run_calls)

    if wrote_browser:
        if _poll_sleep(POST_BATCH_SETTLE_SEC, cancel_check):
            return _strict_return({
                "ok": False,
                "verified": False,
                "cancelled": True,
                "batch_executed": True,
                "executed": executed,
                "phase": "cancelled",
            })
        after_data, _after_source = wait_for_snapshot_change(url, before_fp)
    else:
        after_data, _after_source = load_notebook_snapshot(url)

    run_indices = _ordered_run_indices(
        run_queue_calls,
        wanted_run_cells if wanted_run_cells else None,
    )
    pending_run_cells: list[int] = []
    run_cell_indices: list[int] = []
    if run_indices and run_queue_calls:
        run_cell_indices, run_waits, pending_run_cells = execute_run_queue_sequential(
            run_indices,
            executed=executed,
            registry=registry,
            url=url,
            tab_id=tab_id,
            mode=mode,
            browser_tool_allowed=browser_tool_allowed,
            inter_delay=inter_delay,
            stop_on_error=True,
            cancel_check=cancel_check,
        )
        wrote_browser = True

    verification = verify_workflow_batch(
        before_data=before_data,
        after_data=after_data,
        executed=executed,
        expected_edits=expected_edits,
        run_cell_indices=run_cell_indices,
        run_waits=run_waits,
    )
    if deferred_calls:
        verification["deferred_tool_calls"] = [
            {"tool": c.name, "args": c.args, "id": c.id} for c in deferred_calls
        ]
        verification["deferred_note"] = (
            "Deferred tools were not executed in this batch."
        )

    run_queue = sorted(expected_edits.keys()) if expected_edits else []
    if not run_queue and run_cell_indices and user_requests_run(user_prompt):
        run_queue = sorted(set(run_cell_indices))

    if sequential_pipeline and not pipeline and run_queue and not run_cell_indices:
        if verification.get("verified"):
            pipeline = init_pipeline_state(user_prompt, run_queue, sequential=True)

    if sequential_pipeline and pipeline and pipeline.get("active"):
        if run_cell_indices and run_waits:
            for run_ci, run_wait in zip(run_cell_indices, run_waits):
                pipeline = record_pipeline_run(pipeline, run_ci, run_wait)
        elif (
            verification.get("verified")
            and pipeline.get("pending_runs")
            and not pipeline.get("completed_runs")
            and not run_cell_indices
            and not run_only_batch
        ):
            first_ci = int(pipeline["pending_runs"][0])
            _dispatch_run_cell(
                registry,
                url=url,
                tab_id=tab_id,
                cell_index=first_ci,
                mode=mode,
                browser_tool_allowed=browser_tool_allowed,
            )
            time.sleep(POST_BATCH_SETTLE_SEC)
            snap_for_run, _ = load_notebook_snapshot(url)
            first_wait = wait_for_cell_run(url, first_ci, snap_for_run)
            run_waits = [first_wait]
            run_cell_indices = [first_ci]
            executed.append(
                {
                    "tool": "run_cell",
                    "dispatched": bool(first_wait.get("ok")),
                    "phase": "pipeline_auto_run",
                    "cell_index": first_ci,
                }
            )
            pipeline = record_pipeline_run(pipeline, first_ci, first_wait)
            verification = verify_workflow_batch(
                before_data=before_data,
                after_data=after_data,
                executed=executed,
                expected_edits=expected_edits,
                run_cell_indices=run_cell_indices,
                run_waits=run_waits,
            )

        verification = attach_pipeline_to_verification(
            verification,
            registry=registry,
            url=url,
            pipeline=pipeline,
        )
        if pipeline.get("complete"):
            verification = finalize_tool_queue_verification(
                verification,
                registry=registry,
                url=url,
                expected_edits=expected_edits,
                run_requested=list(pipeline.get("run_queue") or []),
                run_completed=list(pipeline.get("completed_runs") or []),
                run_pending=list(pipeline.get("pending_runs") or []),
                user_prompt=user_prompt,
                run_waits=run_waits,
            )
    elif run_cell_indices or expected_edits:
        verification = finalize_tool_queue_verification(
            verification,
            registry=registry,
            url=url,
            expected_edits=expected_edits,
            run_requested=run_indices,
            run_completed=run_cell_indices,
            run_pending=pending_run_cells,
            user_prompt=user_prompt,
            run_waits=run_waits,
        )
    elif not run_cell_indices and verification.get("verified"):
        verification["await_llm_summary"] = False
        verification["user_response_gate"] = (
            "No run_cell in this batch — summarize for the user in your next reply "
            "(no further write tools unless something failed)."
        )
    verification["batch_executed"] = True
    verification["sequential_pipeline"] = sequential_pipeline
    if read_results:
        verification["read_results"] = read_results

    return _strict_return(verification)
