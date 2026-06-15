"""Strict execution queue — single source of truth for tool batch completion."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT
except Exception:
    from config import DATA_ROOT

STRICT_EXEC_LOG = DATA_ROOT / "logs" / "strict_execution.jsonl"
EXECUTE_FULL_BATCH_WITHOUT_LLM_SPLIT = True

_CELL_INDEX_RE = re.compile(r"\bcell\s*[#:]?\s*(\d+)\b", re.I)


@dataclass
class QueuedOperation:
    index: int
    tool: str
    args: dict[str, Any]
    op_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "args": dict(self.args or {}),
            "op_id": self.op_id,
        }


@dataclass
class RunCellResult:
    cell_index: int
    started: bool = False
    finished: bool = False
    pending: bool = False
    run_verified: bool = False
    success: bool = False
    execution_time: float | None = None
    execution_order: int | None = None
    source: str = ""
    output: str = ""
    traceback: str | None = None
    error_type: str | None = None
    wait_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_index": self.cell_index,
            "started": self.started,
            "finished": self.finished,
            "pending": self.pending,
            "run_verified": self.run_verified,
            "success": self.success,
            "execution_time": self.execution_time,
            "execution_order": self.execution_order,
            "source": self.source[:2000],
            "output": self.output[:8000],
            "traceback": self.traceback,
            "error_type": self.error_type,
            "wait_reason": self.wait_reason,
        }


@dataclass
class ExecutionQueueState:
    operations: list[QueuedOperation] = field(default_factory=list)
    completed_count: int = 0
    queue_complete: bool = False
    primary_target: int | None = None
    edited_cells: set[int] = field(default_factory=set)
    inserted_cells: set[int] = field(default_factory=set)
    run_results: list[RunCellResult] = field(default_factory=list)
    dispatch_failures: list[dict[str, Any]] = field(default_factory=list)
    strict_goal_verified: bool = False
    strict_goal_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operations": [o.to_dict() for o in self.operations],
            "completed_count": self.completed_count,
            "queue_complete": self.queue_complete,
            "primary_target": self.primary_target,
            "edited_cells": sorted(self.edited_cells),
            "inserted_cells": sorted(self.inserted_cells),
            "run_results": [r.to_dict() for r in self.run_results],
            "dispatch_failures": self.dispatch_failures,
            "strict_goal_verified": self.strict_goal_verified,
            "strict_goal_reason": self.strict_goal_reason,
        }


def extract_primary_target(user_prompt: str) -> int | None:
    m = _CELL_INDEX_RE.search(str(user_prompt or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def build_execution_queue(tool_calls: list[Any]) -> ExecutionQueueState:
    """Convert parsed tool batch into ordered queue operations."""
    state = ExecutionQueueState()
    ops: list[QueuedOperation] = []
    for i, tc in enumerate(tool_calls or []):
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "").strip()
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except Exception:
                args = {}
        else:
            name = str(getattr(tc, "name", "") or "")
            args = dict(getattr(tc, "args", None) or {})
        if not name:
            continue
        ops.append(
            QueuedOperation(
                index=len(ops),
                tool=name,
                args=args,
                op_id=str(getattr(tc, "id", None) or tc.get("id") if isinstance(tc, dict) else "") or f"op_{i}",
            )
        )
    state.operations = ops
    return state


def build_run_cell_result(
    cell_index: int,
    wait: dict[str, Any] | None,
    *,
    started_at: float | None = None,
) -> RunCellResult:
    """Normalize wait_for_cell_run payload into mandatory run_cell result."""
    w = dict(wait or {})
    output = str(w.get("output") or "")
    has_output = bool(output.strip())
    run_verified = bool(w.get("run_verified"))

    try:
        from .agentic_batch_executor import analyze_cell_output
    except Exception:
        from agentic_batch_executor import analyze_cell_output

    snap = w.get("run_snapshot") or {}
    after_snap = snap.get("after") or {}

    if has_output:
        a = analyze_cell_output(output)
    else:
        a = {
            "has_error": bool(w.get("has_error")),
            "run_succeeded": w.get("run_succeeded"),
            "pending": not run_verified,
            "error_type": w.get("error_type"),
            "error_summary": w.get("error_summary"),
        }

    explicit_ok = run_verified and not a.get("has_error") and w.get("run_succeeded") is not False
    explicit_fail = run_verified and (w.get("run_succeeded") is False or bool(a.get("has_error")))

    pending = not run_verified or bool(w.get("pending"))
    if run_verified and not a.get("has_error") and w.get("ok") is not False:
        pending = False

    finished = run_verified and not pending
    started = bool(w.get("started")) or run_verified or bool(w.get("ok"))
    has_error = bool(a.get("has_error")) or (run_verified and w.get("run_succeeded") is False)
    success = run_verified and finished and not has_error and not pending and w.get("ok") is not False

    traceback_text = None
    if has_error:
        traceback_text = str(a.get("error_summary") or w.get("error") or output[:2000] or "execution error")

    elapsed = None
    if started_at is not None and finished:
        elapsed = round(time.monotonic() - started_at, 3)

    source = str(after_snap.get("source_preview") or w.get("source") or "")

    return RunCellResult(
        cell_index=int(cell_index),
        started=started,
        finished=finished,
        pending=pending,
        run_verified=run_verified,
        success=success,
        execution_time=elapsed,
        execution_order=w.get("execution_order") or after_snap.get("execution_order"),
        source=source,
        output=output,
        traceback=traceback_text,
        error_type=a.get("error_type") or w.get("error_type"),
        wait_reason=w.get("wait_reason"),
    )


def record_queue_progress(
    state: ExecutionQueueState,
    *,
    tool: str,
    dispatched: bool,
    cell_index: int | None = None,
    error: str | None = None,
) -> None:
    if not dispatched:
        state.dispatch_failures.append({"tool": tool, "cell_index": cell_index, "error": error})
        return
    state.completed_count += 1
    if tool == "edit_cell_by_index" and cell_index is not None:
        state.edited_cells.add(int(cell_index))
    if tool == "insert_cell" and cell_index is not None:
        state.inserted_cells.add(int(cell_index))


def check_target_cell_enforcement(
    state: ExecutionQueueState,
    user_prompt: str,
) -> tuple[bool, str]:
    """Primary target from prompt must be edited or run — not bypassed via workaround cells."""
    primary = extract_primary_target(user_prompt)
    state.primary_target = primary
    if primary is None:
        return True, ""

    prompt_lower = str(user_prompt or "").lower()
    is_fix_request = bool(re.search(r"\b(fix|repair|debug|error)\b", prompt_lower))

    if not is_fix_request:
        return True, ""

    if primary in state.edited_cells:
        return True, ""

    ran_target = any(r.cell_index == primary and r.started for r in state.run_results)
    if ran_target and primary in state.edited_cells:
        return True, ""

    workaround_only = bool(state.inserted_cells) and primary not in state.edited_cells
    if workaround_only or (is_fix_request and primary not in state.edited_cells):
        extras = sorted(state.inserted_cells - {primary})
        if extras or workaround_only:
            return False, f"target_cell_{primary}_not_edited_workaround_cells_{extras}"

    if is_fix_request and primary not in state.edited_cells and not state.run_results:
        return False, f"target_cell_{primary}_never_edited"

    return True, ""


def compute_strict_goal_verified(
    state: ExecutionQueueState,
    *,
    user_prompt: str = "",
    verification: dict[str, Any] | None = None,
    queue_complete: bool = False,
    executor_called: bool = False,
) -> tuple[bool, str]:
    """
    goal_verified=True only if:
    1. target cells edited (when fix request)
    2. target runs executed with mandatory results
    3. verification exists
    4. no execution errors
    5. queue completed
    """
    if not executor_called:
        return False, "executor_never_ran"

    v = verification or {}
    if state.operations and verification is None:
        return False, "verification_missing"

    if state.operations and not queue_complete and not v.get("tool_queue_complete"):
        return False, "queue_incomplete"

    if not v and state.operations:
        return False, "verification_missing"

    target_ok, target_reason = check_target_cell_enforcement(state, user_prompt)
    if not target_ok:
        return False, target_reason

    for r in state.run_results:
        if not r.run_verified:
            return False, f"run_cell_{r.cell_index}_not_verified"
        if r.pending:
            return False, f"run_cell_{r.cell_index}_still_pending"
        if r.started and not r.finished:
            return False, f"run_cell_{r.cell_index}_not_finished"
        if r.finished and not r.success:
            return False, f"run_cell_{r.cell_index}_failed"

    if state.dispatch_failures:
        return False, "dispatch_failures"

    if v.get("execution_error"):
        return False, "execution_error_in_verification"

    if v.get("goal_verified") is False:
        return False, str(v.get("goal_reason") or "goal_verification_false")

    if state.run_results and not all(r.success for r in state.run_results if r.finished):
        return False, "run_verification_failed"

    if state.operations and not queue_complete and not v.get("batch_executed"):
        return False, "batch_not_executed"

    if v and not v.get("verified") and v.get("needs_fix"):
        return False, "verification_needs_fix"

    primary = state.primary_target
    if primary is not None and extract_primary_target(user_prompt):
        fix = re.search(r"\b(fix|repair|debug|error)\b", user_prompt.lower())
        if fix and primary not in state.edited_cells:
            ran_ok = any(r.cell_index == primary and r.success for r in state.run_results)
            if not ran_ok:
                return False, f"target_{primary}_not_verified"

    return True, ""


def build_execution_report(state: ExecutionQueueState) -> dict[str, Any]:
    """Evidence package for LLM — actual execution data only."""
    try:
        from .cell_run_snapshot import format_cell_run_evidence
    except Exception:
        from cell_run_snapshot import format_cell_run_evidence

    executed_lines: list[str] = []
    for op in state.operations:
        ci = op.args.get("cell_index") or op.args.get("index")
        if ci is not None:
            executed_lines.append(f"{op.tool} cell {ci}")
        else:
            executed_lines.append(op.tool)

    results: dict[str, Any] = {}
    formatted_blocks: list[str] = []
    for r in state.run_results:
        key = f"cell_{r.cell_index}"
        entry = {
            "cell_index": r.cell_index,
            "source": r.source[:1500],
            "execution_order": r.execution_order,
            "run_verified": r.run_verified,
            "success": r.success,
            "output": r.output[:2000] if r.output else "",
            "traceback": r.traceback,
            "pending": r.pending,
            "finished": r.finished,
        }
        results[key] = entry
        formatted_blocks.append(format_cell_run_evidence(entry))

    failed = [r for r in state.run_results if r.run_verified and r.finished and not r.success]
    error_block = None
    if failed:
        f0 = failed[0]
        source = f0.source
        if not source:
            for op in state.operations:
                if op.tool == "edit_cell_by_index" and int(op.args.get("cell_index", -1)) == f0.cell_index:
                    source = str(op.args.get("content") or op.args.get("new_source") or "")[:2000]
                    break
        error_block = {
            "TARGET_FAILED": True,
            "cell": f0.cell_index,
            "traceback": f0.traceback,
            "last_source": source,
        }

    report_text = "\n\n".join(formatted_blocks) if formatted_blocks else "EXECUTION REPORT\n\n(no runs in batch)"

    return {
        "EXECUTION_REPORT": True,
        "execution_report_text": report_text,
        "executed": executed_lines,
        "results": results,
        "queue_complete": state.queue_complete,
        "strict_goal_verified": state.strict_goal_verified,
        "primary_target": state.primary_target,
        "edited_cells": sorted(state.edited_cells),
        "error_recovery": error_block,
    }


def format_execution_report_message(report: dict[str, Any]) -> str:
    """Serialize execution report for ReAct context (facts only)."""
    return json.dumps(report, ensure_ascii=False, indent=2)


def attach_strict_execution(
    verification: dict[str, Any],
    *,
    queue_state: ExecutionQueueState,
    user_prompt: str,
    executor_called: bool = True,
) -> dict[str, Any]:
    """Merge strict queue state into verification and recompute goal_verified."""
    out = dict(verification or {})
    queue_state.queue_complete = bool(
        out.get("tool_queue_complete")
        or out.get("run_queue_complete")
        or out.get("tool_queue_status") == "complete"
    )
    strict_ok, strict_reason = compute_strict_goal_verified(
        queue_state,
        user_prompt=user_prompt,
        verification=out,
        queue_complete=queue_state.queue_complete,
        executor_called=executor_called,
    )
    queue_state.strict_goal_verified = strict_ok
    queue_state.strict_goal_reason = strict_reason

    report = build_execution_report(queue_state)
    target_cells = out.get("target_cells") or (out.get("queue_cell_evidence") or {}).get("cells") or []
    if target_cells:
        report["target_cell_query"] = target_cells
        lines = [report.get("execution_report_text") or ""]
        for cell in target_cells:
            if not isinstance(cell, dict):
                continue
            ci = cell.get("cell_index")
            lines.append(
                f"Cell {ci}\n"
                f"Run verified: {'YES' if cell.get('run_verified') else 'NO'}\n"
                f"Success: {'YES' if cell.get('success') else 'NO' if cell.get('success') is False else 'UNKNOWN'}\n"
                f"Source: {str(cell.get('source') or cell.get('input') or '')[:800]}\n"
                + (
                    f"Traceback:\n{cell.get('traceback')}\n"
                    if cell.get("traceback")
                    else f"Output:\n{str(cell.get('output') or '')[:800]}\n"
                )
            )
        report["execution_report_text"] = "\n".join(x for x in lines if x).strip()

    out["execution_queue"] = queue_state.to_dict()
    out["execution_report"] = report
    out["execution_report_text"] = report.get("execution_report_text", "")
    out["strict_goal_verified"] = strict_ok
    out["strict_goal_reason"] = strict_reason
    out["goal_verified"] = strict_ok
    out["goal_reason"] = strict_reason if not strict_ok else out.get("goal_reason", "strict queue verified")

    had_runs = bool(queue_state.run_results)
    if had_runs:
        out["continue_react_loop"] = True
        out["close_react_loop"] = False
        out["await_llm_summary"] = strict_ok
        out["run_evidence_delivered"] = True

    if not strict_ok:
        out["verified"] = False
        out["needs_fix"] = True
        out["await_llm_summary"] = False
        out["close_react_loop"] = False
        out["continue_react_loop"] = True
        if report.get("error_recovery"):
            er = report["error_recovery"]
            out["execution_error"] = {
                "cell_index": er.get("cell"),
                "error_summary": er.get("traceback"),
                "error_type": "ExecutionError",
            }
            out["user_response_gate"] = (
                f"TARGET FAILED\nCell: {er.get('cell')}\n"
                f"Traceback:\n{er.get('traceback')}\n"
                f"Last source:\n{er.get('last_source', '')[:1500]}"
            )
    else:
        if not had_runs:
            out["await_llm_summary"] = out.get("await_llm_summary", True)

    _append_strict_log(out, queue_state)
    return out


def _append_strict_log(verification: dict[str, Any], state: ExecutionQueueState) -> None:
    try:
        STRICT_EXEC_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "strict_goal_verified": state.strict_goal_verified,
            "strict_goal_reason": state.strict_goal_reason,
            "queue_complete": state.queue_complete,
            "parsed_ops": len(state.operations),
            "run_results": [r.to_dict() for r in state.run_results],
        }
        with STRICT_EXEC_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
