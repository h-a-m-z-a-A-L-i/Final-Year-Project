"""Compact batch verification payloads for ReAct tool rounds."""

from __future__ import annotations

import json
from collections import defaultdict
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
                    "input_preview": str(c.get("input") or c.get("source") or "")[:200],
                    "output_preview": str(c.get("output") or "")[:300],
                    "run_verified": c.get("run_verified"),
                    "success": c.get("success"),
                    "traceback": str(c.get("traceback") or "")[:200] or None,
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
        "kernel_session": verification.get("kernel_session"),
        "batch_audit": {
            k: verification.get("batch_audit", {}).get(k)
            for k in (
                "tools_requested", "tools_executed", "tools_verified", "tools_failed",
                "goal_verified", "failed_cells", "next_required_action",
            )
            if isinstance(verification.get("batch_audit"), dict)
        } or None,
    }


def _parse_tool_call_entry(tc: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
    fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
    name = str(fn.get("name") or "")
    raw_args = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except Exception:
        args = {}
    tool_call_id = tc.get("id") if isinstance(tc, dict) else None
    return name, args, str(tool_call_id) if tool_call_id else None


def _cell_index_from_args(args: dict[str, Any]) -> int | None:
    for key in ("cell_index", "index"):
        try:
            if args.get(key) is not None:
                return int(args[key])
        except (TypeError, ValueError):
            continue
    return None


def _build_single_tool_result(
    tool_name: str,
    args: dict[str, Any],
    *,
    verification: dict[str, Any],
    deferred_ids: set[str],
    tool_call_id: str | None,
    executed_pool: dict[tuple[str, int | None], list[dict[str, Any]]],
    tv_pool: dict[tuple[str, int | None], list[dict[str, Any]]],
    run_wait_by_cell: dict[int, dict[str, Any]],
    cell_evidence: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    cell_index = _cell_index_from_args(args)
    key = (tool_name, cell_index)

    if tool_call_id and tool_call_id in deferred_ids:
        return {
            "ok": False,
            "deferred": True,
            "tool": tool_name,
            "cell_index": cell_index,
            "note": "Deferred to a later host batch — not executed this round.",
        }

    executed = (executed_pool.get(key) or []).pop(0) if executed_pool.get(key) else None
    tv = (tv_pool.get(key) or []).pop(0) if tv_pool.get(key) else None

    result: dict[str, Any] = {
        "ok": bool((executed or {}).get("dispatched")) if executed else bool(
            (tv or {}).get("verification_status") == "verified"
        ),
        "tool": tool_name,
        "cell_index": cell_index,
    }
    if executed:
        result["phase"] = executed.get("phase")
        if executed.get("error"):
            result["error"] = executed["error"]
    if tv:
        result["verification_status"] = tv.get("verification_status")
        if tv.get("reason"):
            result["reason"] = tv.get("reason")
        evidence = tv.get("evidence") or {}
        if evidence.get("output_preview"):
            result["output_preview"] = evidence.get("output_preview")

    if tool_name == "run_cell" and cell_index is not None:
        wait = run_wait_by_cell.get(int(cell_index)) or {}
        cell = cell_evidence.get(int(cell_index)) or {}
        output = wait.get("output") or cell.get("output") or ""
        result["run_verified"] = wait.get("run_verified")
        result["run_completed"] = wait.get("run_completed")
        result["run_succeeded"] = wait.get("run_succeeded")
        result["pending"] = wait.get("pending")
        if wait.get("error"):
            result["error"] = wait.get("error")
        if wait.get("error_summary"):
            result["error_summary"] = wait.get("error_summary")
        elif wait.get("has_error"):
            result["error_summary"] = wait.get("error_summary") or "execution error"
        if output:
            result["output_preview"] = str(output)[:400]
        if wait.get("run_cell_result"):
            result["run_cell_result"] = wait.get("run_cell_result")
        err = verification.get("execution_error") or {}
        if isinstance(err, dict) and err.get("cell_index") == cell_index:
            result["ok"] = False
            result["run_succeeded"] = False
            result["error_summary"] = err.get("error_summary") or result.get("error_summary")

    if tool_name == "edit_cell_by_index" and cell_index is not None:
        cell = cell_evidence.get(int(cell_index)) or {}
        preview = cell.get("input") or cell.get("source") or cell.get("content")
        if preview:
            result["input_preview"] = str(preview)[:200]

    if not executed and not tv and tool_name in {"insert_cell", "delete_by_index", "creating_markdown_by_index"}:
        # Writes may not have per-tool verification rows — infer from batch status.
        batch_ok = verification.get("batch_executed") and not verification.get("needs_fix")
        result["ok"] = bool(batch_ok)
        result["phase"] = "batch_write"

    return result


def append_native_batch_tool_results(
    tool_messages: list[dict[str, Any]],
    batch_tool_calls: list[dict[str, Any]],
    verification: dict[str, Any],
    *,
    round_idx: int,
) -> None:
    """
    Append one role=tool message per native API tool_call_id (required by OpenAI/Cerebras).
    """
    try:
        from .streaming import _compact_tool_result_content
    except Exception:
        try:
            from streaming import _compact_tool_result_content
        except Exception:
            def _compact_tool_result_content(text: str) -> str:
                return text

    deferred_ids = {
        str(d.get("id"))
        for d in (verification.get("deferred_tool_calls") or [])
        if isinstance(d, dict) and d.get("id")
    }

    executed_pool: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for row in verification.get("executed") or []:
        if not isinstance(row, dict):
            continue
        ci = row.get("cell_index")
        try:
            ci = int(ci) if ci is not None else None
        except (TypeError, ValueError):
            ci = None
        executed_pool[(str(row.get("tool") or ""), ci)].append(row)

    tv_pool: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for row in verification.get("tool_verifications") or []:
        if not isinstance(row, dict):
            continue
        ci = (row.get("evidence") or {}).get("cell_index")
        try:
            ci = int(ci) if ci is not None else None
        except (TypeError, ValueError):
            ci = None
        tv_pool[(str(row.get("tool") or ""), ci)].append(row)

    run_wait_by_cell: dict[int, dict[str, Any]] = {}
    for wait in verification.get("run_waits") or []:
        if isinstance(wait, dict) and wait.get("cell_index") is not None:
            try:
                run_wait_by_cell[int(wait["cell_index"])] = wait
            except (TypeError, ValueError):
                pass

    evidence = verification.get("queue_cell_evidence") or {}
    cell_evidence: dict[int, dict[str, Any]] = {}
    for cell in (evidence.get("cells") or []) if isinstance(evidence, dict) else []:
        if isinstance(cell, dict) and cell.get("cell_index") is not None:
            try:
                cell_evidence[int(cell["cell_index"])] = cell
            except (TypeError, ValueError):
                pass

    for tc_idx, tc in enumerate(batch_tool_calls or []):
        if not isinstance(tc, dict):
            continue
        tool_name, args, tool_call_id = _parse_tool_call_entry(tc)
        if not tool_call_id:
            tool_call_id = f"call_{tool_name or 'tool'}_{round_idx}_{tc_idx}"
        payload = _build_single_tool_result(
            tool_name,
            args,
            verification=verification,
            deferred_ids=deferred_ids,
            tool_call_id=tool_call_id,
            executed_pool=executed_pool,
            tv_pool=tv_pool,
            run_wait_by_cell=run_wait_by_cell,
            cell_evidence=cell_evidence,
        )
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": _compact_tool_result_content(
                    json.dumps(payload, ensure_ascii=False)
                ),
            }
        )


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
    report_text = verification.get("execution_report_text") or (
        (report or {}).get("execution_report_text") if isinstance(report, dict) else None
    )
    if report:
        compact["execution_report"] = report
    if report_text:
        compact["execution_report_text"] = report_text
    if verification.get("continue_react_loop"):
        compact["continue_react_loop"] = True
    if verification.get("user_response_gate") and verification.get("strict_goal_verified") is False:
        compact["user_response_gate"] = str(verification.get("user_response_gate") or "")[:1500]
    if compact_fn:
        compact = compact_fn(verification)
    payload = _compact_tool_result_content(json.dumps(compact, ensure_ascii=False))
    content_parts = [VERIFICATION_MARKER, payload]
    if report_text:
        content_parts.append(str(report_text))
    tool_messages.append(
        {
            "role": "user",
            "content": "\n".join(content_parts),
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
