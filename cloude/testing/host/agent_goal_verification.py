"""Goal-aware verification — tool success ≠ task success."""

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

try:
    from .agentic_batch_executor import analyze_cell_output, _content_matches
except Exception:
    try:
        from agentic_batch_executor import analyze_cell_output, _content_matches
    except Exception:
        def analyze_cell_output(output: str | None) -> dict[str, Any]:
            text = str(output or "")
            has_err = "error" in text.lower() or "traceback" in text.lower()
            return {
                "has_error": has_err,
                "run_succeeded": not has_err,
                "error_type": "Error" if has_err else None,
                "error_summary": text[:200] if has_err else None,
            }

        def _content_matches(expected: str, actual: str) -> bool:
            return str(expected or "").strip() in str(actual or "")

try:
    from .cell_structure_observer import (
        verify_cell_count_decreased,
        verify_cell_count_increased,
    )
except Exception:
    try:
        from cell_structure_observer import (
            verify_cell_count_decreased,
            verify_cell_count_increased,
        )
    except Exception:
        def verify_cell_count_increased(*_a, **_k) -> dict[str, Any]:
            return {"ok": False, "verified": False}

        def verify_cell_count_decreased(*_a, **_k) -> dict[str, Any]:
            return {"ok": False, "verified": False}

try:
    from .cell_execution_observer import verify_cell_ran
except Exception:
    try:
        from cell_execution_observer import verify_cell_ran
    except Exception:
        def verify_cell_ran(*_a, **_k) -> dict[str, Any]:
            return {"ok": False, "verified": False}

VERIFICATION_LOG_PATH = DATA_ROOT / "logs" / "agent_verification.log"
_LOG_LOCK = threading.Lock()

_SUCCESS_CLAIM_RE = re.compile(
    r"\b("
    r"fixed|successfully|verified|resolved|completed|no error|error has been fixed|"
    r"execution successful|task complete|all good|working now"
    r")\b",
    re.I,
)

_CELL_INDEX_RE = re.compile(r"\bcell\s*[#:]?\s*(\d+)\b", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tool_verification_record(
    tool: str,
    *,
    tool_called: bool = True,
    tool_succeeded: bool = False,
    verification_status: str = "unknown",
    evidence: dict[str, Any] | None = None,
    reason: str = "",
    next_action_required: bool = False,
) -> dict[str, Any]:
    return {
        "tool": tool,
        "tool_called": tool_called,
        "tool_succeeded": tool_succeeded,
        "verification_status": verification_status,
        "evidence": evidence or {},
        "reason": reason,
        "next_action_required": next_action_required,
    }


def verify_edit_cell(
    cell_index: int,
    expected_content: str,
    actual_source: str | None,
) -> dict[str, Any]:
    actual = str(actual_source or "")
    expected = str(expected_content or "")
    if not expected.strip():
        return tool_verification_record(
            "edit_cell_by_index",
            tool_succeeded=True,
            verification_status="unknown",
            evidence={"cell_index": cell_index},
            reason="No expected content to compare",
        )
    matches = _content_matches(expected, actual)
    status = "verified" if matches else "failed"
    return tool_verification_record(
        "edit_cell_by_index",
        tool_succeeded=True,
        verification_status=status,
        evidence={
            "cell_index": cell_index,
            "edit_applied": bool(actual.strip()),
            "source_matches_expected": matches,
            "expected_preview": expected[:200],
            "actual_preview": actual[:200],
        },
        reason="" if matches else f"Cell {cell_index} source does not match expected edit",
        next_action_required=not matches,
    )


def verify_insert_cell(
    *,
    before_cells: list[dict[str, Any]] | None,
    after_cells: list[dict[str, Any]] | None,
    expected_delta: int = 1,
    expected_indices: list[int] | None = None,
) -> dict[str, Any]:
    check = verify_cell_count_increased(
        before_cells,
        after_cells,
        expected_delta=expected_delta,
        expected_indices=expected_indices,
    )
    ok = bool(check.get("ok")) or len(check.get("new_indices") or []) >= int(expected_delta)
    status = "verified" if ok else "failed"
    return tool_verification_record(
        "insert_cell",
        tool_succeeded=True,
        verification_status=status,
        evidence={
            "expected_delta": expected_delta,
            "new_indices": check.get("new_indices"),
            "count_before": check.get("count_before"),
            "count_after": check.get("count_after"),
            "count_delta": check.get("count_delta"),
            "missing_expected_indices": check.get("missing_expected_indices"),
        },
        reason="" if ok else "Insert did not increase notebook cell count",
        next_action_required=not ok,
    )


def verify_delete_cell(
    *,
    before_cells: list[dict[str, Any]] | None,
    after_cells: list[dict[str, Any]] | None,
    cell_index: int | None = None,
    expected_delta: int = 1,
) -> dict[str, Any]:
    expected_indices = [int(cell_index)] if cell_index is not None else None
    check = verify_cell_count_decreased(
        before_cells,
        after_cells,
        expected_delta=expected_delta,
        expected_indices=expected_indices,
    )
    ok = bool(check.get("ok")) or len(check.get("removed_indices") or []) >= int(expected_delta)
    status = "verified" if ok else "failed"
    return tool_verification_record(
        "delete_by_index",
        tool_succeeded=True,
        verification_status=status,
        evidence={
            "cell_index": cell_index,
            "expected_delta": expected_delta,
            "removed_indices": check.get("removed_indices"),
            "count_before": check.get("count_before"),
            "count_after": check.get("count_after"),
            "count_delta": check.get("count_delta"),
            "missing_expected_indices": check.get("missing_expected_indices"),
        },
        reason="" if ok else f"Delete did not remove cell(s) from notebook",
        next_action_required=not ok,
    )


def verify_run_cell(
    cell_index: int,
    *,
    run_wait: dict[str, Any] | None = None,
    cell_output: str | None = None,
    before_cells: list[dict[str, Any]] | None = None,
    after_cells: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    output = str(cell_output if cell_output is not None else (run_wait or {}).get("output") or "")
    analysis = analyze_cell_output(output)
    dispatched = bool((run_wait or {}).get("ok", True))
    run_verified = bool((run_wait or {}).get("run_verified"))
    if (run_wait or {}).get("run_cell_result"):
        run_verified = bool(run_wait["run_cell_result"].get("run_verified"))
    if before_cells is not None and after_cells is not None:
        observer = verify_cell_ran(before_cells, after_cells, int(cell_index))
        if observer.get("verified"):
            run_verified = True
        elif run_verified and not observer.get("verified"):
            run_verified = False
    executed = dispatched and (
        run_verified
        or bool(output or (run_wait or {}).get("run_completed"))
    )
    has_error = bool(analysis.get("has_error")) or (run_wait or {}).get("run_succeeded") is False
    error_type = analysis.get("error_type") or (run_wait or {}).get("error_type")
    error_message = analysis.get("error_summary") or (run_wait or {}).get("error_summary")

    if not dispatched:
        return tool_verification_record(
            "run_cell",
            tool_succeeded=False,
            verification_status="failed",
            evidence={"cell_index": cell_index, "executed": False},
            reason=(run_wait or {}).get("error") or "run_cell dispatch failed",
            next_action_required=True,
        )

    if dispatched and not run_verified:
        return tool_verification_record(
            "run_cell",
            tool_succeeded=False,
            verification_status="failed",
            evidence={
                "cell_index": cell_index,
                "executed": False,
                "execution_state": "not_verified",
                "output_preview": output[:400],
            },
            reason=f"Cell {cell_index} run not verified in notebook snapshot",
            next_action_required=True,
        )

    if has_error:
        return tool_verification_record(
            "run_cell",
            tool_succeeded=True,
            verification_status="failed",
            evidence={
                "cell_index": cell_index,
                "executed": True,
                "execution_state": "error",
                "error_type": error_type,
                "error_message": error_message,
                "output_preview": output[:400],
            },
            reason=f"Cell {cell_index} execution error: {error_message or error_type or 'unknown'}",
            next_action_required=True,
        )

    return tool_verification_record(
        "run_cell",
        tool_succeeded=True,
        verification_status="verified",
        evidence={
            "cell_index": cell_index,
            "executed": True,
            "run_verified": True,
            "execution_state": "completed",
            "error": None,
            "output_preview": output[:400],
        },
        reason="",
        next_action_required=False,
    )


def extract_cell_index_from_prompt(prompt: str) -> int | None:
    m = _CELL_INDEX_RE.search(str(prompt or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def classify_goal_type(user_prompt: str) -> str:
    text = str(user_prompt or "").lower()
    if re.search(r"\b(fix|repair|debug|resolve|correct)\b.*\b(error|bug|exception|traceback)\b", text):
        return "fix_error"
    if re.search(r"\b(fix|repair)\b.*\bcell\b", text):
        return "fix_error"
    if any(w in text for w in ("visualiz", "plot", "chart", "figure", "graph", "histogram", "heatmap")):
        return "visualization"
    if re.search(r"\b(train|fit)\b.*\b(model|classifier|regressor|network|nn)\b", text) or "train model" in text:
        return "train_model"
    if re.search(r"\b(submission\.csv|\.csv\b|save.*file|write.*file|export)\b", text):
        return "create_file"
    if re.search(r"\b(run|execute)\b.*\bcell\b", text):
        return "execute_cell"
    return "generic"


def _cell_evidence_map(evidence: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    cells = (evidence or {}).get("cells") if isinstance(evidence, dict) else None
    if not isinstance(cells, list):
        return out
    for c in cells:
        if not isinstance(c, dict):
            continue
        try:
            out[int(c.get("cell_index"))] = c
        except (TypeError, ValueError):
            continue
    return out


def _output_shows_visualization(output: str) -> bool:
    low = output.lower()
    if "<img" in low or "image/png" in low or "display(" in low:
        return True
    if re.search(r"\b(plt\.show|fig\.|figure|axes|subplot)\b", output, re.I):
        return True
    return bool(re.search(r"savefig\s*\(", output, re.I))


def _output_shows_file(output: str, prompt: str) -> bool:
    low = output.lower()
    if "submission.csv" in low or "/kaggle/working/" in low:
        return True
    if re.search(r"\b(wrote|saved|writing)\b.*\.(csv|json|pth|pkl)\b", low):
        return True
    if "submission.csv" in prompt.lower() and ".csv" in low:
        return True
    return False


def verify_user_goal(
    user_prompt: str,
    *,
    tool_verifications: list[dict[str, Any]],
    evidence: dict[str, Any] | None = None,
    run_completed: list[int] | None = None,
    expected_edits: dict[int, str] | None = None,
) -> dict[str, Any]:
    goal_type = classify_goal_type(user_prompt)
    target_cell = extract_cell_index_from_prompt(user_prompt)
    cell_map = _cell_evidence_map(evidence)
    runs = list(run_completed or [])
    edits = dict(expected_edits or {})

    run_by_cell = {
        int((tv.get("evidence") or {}).get("cell_index")): tv
        for tv in tool_verifications
        if tv.get("tool") == "run_cell" and (tv.get("evidence") or {}).get("cell_index") is not None
    }
    edit_by_cell = {
        int((tv.get("evidence") or {}).get("cell_index")): tv
        for tv in tool_verifications
        if tv.get("tool") == "edit_cell_by_index" and (tv.get("evidence") or {}).get("cell_index") is not None
    }

    if goal_type == "fix_error":
        ci = target_cell
        if ci is None and runs:
            ci = runs[-1]
        if ci is None:
            return {"goal_verified": False, "goal_reason": "Fix-error task requires a target cell index", "goal_type": goal_type}
        run_tv = run_by_cell.get(ci)
        cell_out = str((cell_map.get(ci) or {}).get("output") or "")
        if run_tv is None and ci not in runs:
            return {
                "goal_verified": False,
                "goal_reason": f"Cell {ci} was not executed — run_cell required to verify fix",
                "goal_type": goal_type,
                "target_cell": ci,
            }
        if run_tv and run_tv.get("verification_status") == "failed":
            err = (run_tv.get("evidence") or {}).get("error_message") or run_tv.get("reason")
            return {
                "goal_verified": False,
                "goal_reason": f"Cell {ci} still raises error: {err}",
                "goal_type": goal_type,
                "target_cell": ci,
            }
        analysis = analyze_cell_output(cell_out)
        if analysis.get("has_error"):
            return {
                "goal_verified": False,
                "goal_reason": f"Cell {ci} output still contains error: {analysis.get('error_summary')}",
                "goal_type": goal_type,
                "target_cell": ci,
            }
        if not ci in edits and not edit_by_cell.get(ci):
            if analysis.get("has_output") and not analysis.get("has_error"):
                return {
                    "goal_verified": True,
                    "goal_reason": f"Cell {ci} executes without exception",
                    "goal_type": goal_type,
                    "target_cell": ci,
                }
        return {
            "goal_verified": True,
            "goal_reason": f"Cell {ci} fixed and verified — no exception in output",
            "goal_type": goal_type,
            "target_cell": ci,
        }

    if goal_type == "visualization":
        for ci in reversed(runs or list(cell_map.keys())):
            out = str((cell_map.get(ci) or {}).get("output") or "")
            tv = run_by_cell.get(ci)
            if tv and tv.get("verification_status") == "failed":
                continue
            if _output_shows_visualization(out):
                return {"goal_verified": True, "goal_reason": f"Visualization output detected in cell {ci}", "goal_type": goal_type}
        return {"goal_verified": False, "goal_reason": "No visualization output verified in executed cells", "goal_type": goal_type}

    if goal_type == "train_model":
        for ci in reversed(runs or list(cell_map.keys())):
            tv = run_by_cell.get(ci)
            out = str((cell_map.get(ci) or {}).get("output") or "")
            if tv and tv.get("verification_status") == "failed":
                return {"goal_verified": False, "goal_reason": f"Training failed in cell {ci}: {tv.get('reason')}", "goal_type": goal_type}
            if tv and tv.get("verification_status") == "verified" and out.strip():
                return {"goal_verified": True, "goal_reason": f"Model training cell {ci} completed without error", "goal_type": goal_type}
        return {"goal_verified": False, "goal_reason": "Model training not verified — no successful run output", "goal_type": goal_type}

    if goal_type == "create_file":
        prompt_low = user_prompt.lower()
        for ci in reversed(runs or list(cell_map.keys())):
            out = str((cell_map.get(ci) or {}).get("output") or "")
            if _output_shows_file(out, user_prompt):
                return {"goal_verified": True, "goal_reason": f"File output detected from cell {ci}", "goal_type": goal_type}
        if "submission.csv" in prompt_low:
            return {"goal_verified": False, "goal_reason": "submission.csv not verified in cell outputs", "goal_type": goal_type}
        return {"goal_verified": False, "goal_reason": "File creation not verified in outputs", "goal_type": goal_type}

    if goal_type == "execute_cell":
        ci = target_cell
        if ci is not None:
            tv = run_by_cell.get(ci)
            if tv and tv.get("verification_status") == "verified":
                return {"goal_verified": True, "goal_reason": f"Cell {ci} executed successfully", "goal_type": goal_type}
            if tv and tv.get("verification_status") == "failed":
                return {"goal_verified": False, "goal_reason": tv.get("reason") or f"Cell {ci} execution failed", "goal_type": goal_type}
            return {"goal_verified": False, "goal_reason": f"Cell {ci} was not executed", "goal_type": goal_type}

    failed = [tv for tv in tool_verifications if tv.get("verification_status") == "failed"]
    if failed:
        return {
            "goal_verified": False,
            "goal_reason": failed[0].get("reason") or "One or more tools failed verification",
            "goal_type": goal_type,
        }
    if runs:
        return {"goal_verified": True, "goal_reason": "All executed cells verified", "goal_type": goal_type}
    if edits:
        unverified_edits = [tv for tv in tool_verifications if tv.get("tool") == "edit_cell_by_index" and tv.get("verification_status") != "verified"]
        if unverified_edits:
            return {"goal_verified": False, "goal_reason": unverified_edits[0].get("reason") or "Edit not verified", "goal_type": goal_type}
        return {"goal_verified": True, "goal_reason": "Edits verified (no run required)", "goal_type": goal_type}
    structure_verified = [
        tv for tv in tool_verifications
        if tv.get("tool") in ("insert_cell", "delete_by_index", "creating_markdown_by_index")
        and tv.get("verification_status") == "verified"
    ]
    if structure_verified:
        return {
            "goal_verified": True,
            "goal_reason": "Notebook structure changes verified (no run required)",
            "goal_type": goal_type,
        }
    return {"goal_verified": True, "goal_reason": "No executable goal constraints", "goal_type": goal_type}


def build_tool_verifications(
    verification: dict[str, Any],
    *,
    expected_edits: dict[int, str] | None = None,
    run_waits: list[dict[str, Any]] | None = None,
    run_indices: list[int] | None = None,
) -> list[dict[str, Any]]:
    edits = dict(expected_edits or {})
    runs = list(run_indices or [])
    waits = list(run_waits or [])
    evidence = verification.get("queue_cell_evidence") or {}
    cell_map = _cell_evidence_map(evidence)
    before_cells = verification.get("structure_before")
    after_cells = verification.get("structure_after")
    out: list[dict[str, Any]] = []

    insert_count = sum(
        1 for row in (verification.get("executed") or [])
        if isinstance(row, dict) and row.get("tool") == "insert_cell"
    )
    if insert_count and before_cells is not None and after_cells is not None:
        out.append(
            verify_insert_cell(
                before_cells=before_cells,
                after_cells=after_cells,
                expected_delta=insert_count,
            )
        )

    delete_rows = [
        row for row in (verification.get("executed") or [])
        if isinstance(row, dict) and row.get("tool") == "delete_by_index"
    ]
    if delete_rows and before_cells is not None and after_cells is not None:
        for row in delete_rows:
            ci = (row.get("cell_index") or row.get("index"))
            try:
                ci = int(ci) if ci is not None else None
            except (TypeError, ValueError):
                ci = None
            out.append(
                verify_delete_cell(
                    before_cells=before_cells,
                    after_cells=after_cells,
                    cell_index=ci,
                    expected_delta=1,
                )
            )

    for ci, content in edits.items():
        cell = cell_map.get(int(ci)) or {}
        actual = cell.get("input") or cell.get("source") or cell.get("content")
        out.append(verify_edit_cell(int(ci), content, str(actual or "")))

    for idx, ci in enumerate(runs):
        wait = waits[idx] if idx < len(waits) else {}
        cell = cell_map.get(int(ci)) or {}
        output = cell.get("output") if cell.get("output") is not None else wait.get("output")
        out.append(
            verify_run_cell(
                int(ci),
                run_wait=wait,
                cell_output=str(output or ""),
                before_cells=before_cells,
                after_cells=after_cells,
            )
        )

    for row in verification.get("batch") or []:
        if not isinstance(row, dict):
            continue
        tool = row.get("tool")
        if tool == "run_cell" and not any(tv.get("tool") == "run_cell" and (tv.get("evidence") or {}).get("cell_index") == row.get("cell_index") for tv in out):
            ci = row.get("cell_index")
            if ci is not None:
                cell = cell_map.get(int(ci)) or {}
                out.append(
                    verify_run_cell(
                        int(ci),
                        cell_output=str(cell.get("output") or ""),
                        before_cells=before_cells,
                        after_cells=after_cells,
                    )
                )
        if tool == "edit_cell_by_index" and row.get("cell_index") is not None:
            ci = int(row["cell_index"])
            if not any(tv.get("tool") == "edit_cell_by_index" and (tv.get("evidence") or {}).get("cell_index") == ci for tv in out):
                cell = cell_map.get(ci) or {}
                out.append(verify_edit_cell(ci, "", str(cell.get("input") or "")))
        if tool == "insert_cell" and not any(tv.get("tool") == "insert_cell" for tv in out):
            if before_cells is not None and after_cells is not None:
                out.append(
                    verify_insert_cell(
                        before_cells=before_cells,
                        after_cells=after_cells,
                        expected_delta=1,
                    )
                )
        if tool == "delete_by_index" and row.get("cell_index") is not None:
            ci = row.get("cell_index")
            if not any(
                tv.get("tool") == "delete_by_index"
                and (tv.get("evidence") or {}).get("cell_index") == ci
                for tv in out
            ):
                if before_cells is not None and after_cells is not None:
                    out.append(
                        verify_delete_cell(
                            before_cells=before_cells,
                            after_cells=after_cells,
                            cell_index=int(ci),
                            expected_delta=1,
                        )
                    )

    return out


def build_batch_audit(
    verification: dict[str, Any],
    tool_verifications: list[dict[str, Any]],
    goal_result: dict[str, Any],
) -> dict[str, Any]:
    executed = verification.get("executed") or []
    tools_requested = len(executed)
    tools_executed = sum(1 for e in executed if isinstance(e, dict) and (e.get("dispatched") or e.get("ok")))
    verified_tools = [tv for tv in tool_verifications if tv.get("verification_status") == "verified"]
    failed_tools = [tv for tv in tool_verifications if tv.get("verification_status") == "failed"]
    failed_cells: list[int] = []
    for tv in failed_tools:
        ci = (tv.get("evidence") or {}).get("cell_index")
        if ci is not None:
            try:
                failed_cells.append(int(ci))
            except (TypeError, ValueError):
                pass

    next_action = ""
    if not goal_result.get("goal_verified"):
        next_action = goal_result.get("goal_reason") or "Continue tool usage until goal is verified"
        if failed_tools:
            next_action = failed_tools[0].get("reason") or next_action

    return {
        "tools_requested": tools_requested,
        "tools_executed": tools_executed,
        "tools_verified": len(verified_tools),
        "tools_failed": len(failed_tools),
        "goal_verified": bool(goal_result.get("goal_verified")),
        "goal_type": goal_result.get("goal_type"),
        "goal_reason": goal_result.get("goal_reason"),
        "failed_cells": sorted(set(failed_cells)),
        "next_required_action": next_action,
        "tool_results": tool_verifications,
    }


def apply_goal_verification_layer(
    verification: dict[str, Any],
    *,
    user_prompt: str = "",
    expected_edits: dict[int, str] | None = None,
    run_waits: list[dict[str, Any]] | None = None,
    run_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Enrich verification with per-tool + goal checks; downgrade false success."""
    if not isinstance(verification, dict):
        return verification

    out = dict(verification)
    tool_v = build_tool_verifications(
        out,
        expected_edits=expected_edits,
        run_waits=run_waits,
        run_indices=run_indices,
    )
    goal = verify_user_goal(
        user_prompt,
        tool_verifications=tool_v,
        evidence=out.get("queue_cell_evidence"),
        run_completed=list((out.get("tool_queue") or {}).get("run_completed") or out.get("runs_executed") or run_indices or []),
        expected_edits=expected_edits,
    )
    audit = build_batch_audit(out, tool_v, goal)

    out["tool_verifications"] = tool_v
    out["goal_verification"] = goal
    out["batch_audit"] = audit
    out["goal_verified"] = bool(goal.get("goal_verified"))
    out["goal_reason"] = goal.get("goal_reason") or ""

    any_failed = any(tv.get("verification_status") == "failed" for tv in tool_v)
    if any_failed or not out["goal_verified"]:
        out["verified"] = False
        out["needs_fix"] = True
        out["next_action_required"] = True
        out["await_llm_summary"] = False
        out["close_react_loop"] = False

        failed_run = next((tv for tv in tool_v if tv.get("tool") == "run_cell" and tv.get("verification_status") == "failed"), None)
        if failed_run:
            ev = failed_run.get("evidence") or {}
            out["execution_error"] = {
                "cell_index": ev.get("cell_index"),
                "error_type": ev.get("error_type"),
                "error_summary": ev.get("error_message") or failed_run.get("reason"),
                "output_preview": ev.get("output_preview"),
            }
            out["tool_queue_status"] = "error"
            out["tool_queue_stopped"] = True
            out["run_queue_stopped"] = True
        elif out.get("tool_queue_status") == "complete":
            out["tool_queue_status"] = "verification_failed"
            out["tool_queue_complete"] = False

        gate = audit.get("next_required_action") or goal.get("goal_reason") or "Goal not verified — continue with tools."
        out["user_response_gate"] = (
            f"VERIFICATION FAILED — do not claim success. {gate} "
            "Inspect queue_cell_evidence, fix with edit_cell_by_index, run_cell, emit a new tool batch."
        )
    else:
        out["next_action_required"] = False
        if out.get("tool_queue_status") == "complete" or out.get("tool_queue_complete"):
            out["await_llm_summary"] = True
            out["close_react_loop"] = True
            out["user_response_gate"] = (
                "Goal verified. Summarize what changed using evidence from queue_cell_evidence — "
                "you may state success only because goal_verified=true."
            )

    log_verification_batch(
        goal=user_prompt,
        verification=out,
        audit=audit,
        response_allowed=bool(out.get("goal_verified")),
    )
    return out


def goal_verification_failed(verification: dict[str, Any] | None) -> bool:
    if not isinstance(verification, dict):
        return False
    if verification.get("goal_verified") is False:
        return True
    if verification.get("tool_queue_status") == "verification_failed":
        return True
    return False


def sanitize_false_success_language(text: str, verification: dict[str, Any] | None) -> str:
    """Strip hallucinated success claims when goal_verified is false."""
    if not text or not isinstance(verification, dict):
        return text
    if verification.get("goal_verified") is not False:
        return text
    if not _SUCCESS_CLAIM_RE.search(text):
        return text

    audit = verification.get("batch_audit") or {}
    goal = verification.get("goal_verification") or {}
    reason = audit.get("next_required_action") or goal.get("goal_reason") or verification.get("goal_reason")
    failed_cells = audit.get("failed_cells") or []
    prefix = "The requested task could not be verified."
    if failed_cells:
        prefix = f"Cell {failed_cells[0]} verification failed."
    if reason:
        return f"{prefix}\n\n{reason}\n\n(Success language removed — goal_verified=false.)"
    return f"{prefix}\n\n(Success language removed — goal_verified=false.)"


def log_verification_batch(
    *,
    goal: str,
    verification: dict[str, Any],
    audit: dict[str, Any],
    response_allowed: bool,
) -> None:
    entry = {
        "ts": _now_iso(),
        "goal": str(goal or "")[:500],
        "tool_results": audit.get("tool_results") or verification.get("tool_verifications") or [],
        "verification_status": verification.get("tool_queue_status"),
        "goal_verified": verification.get("goal_verified"),
        "goal_reason": verification.get("goal_reason"),
        "batch_audit": {
            k: audit.get(k)
            for k in (
                "tools_requested", "tools_executed", "tools_verified", "tools_failed",
                "goal_verified", "failed_cells", "next_required_action",
            )
        },
        "response_allowed": response_allowed,
    }
    try:
        VERIFICATION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with VERIFICATION_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
