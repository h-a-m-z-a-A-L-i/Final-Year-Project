"""Sequential run-verify pipeline for multi-step notebook workflows."""

from __future__ import annotations

import re
from typing import Any

_PIPELINE_PROMPT_MARKERS = (
    "import",
    "load",
    "read_csv",
    "step",
    "then ",
    "pipeline",
    "analyze",
    "train",
    "fit",
    "preprocess",
    "print top",
    "head(",
    "display",
    "multistep",
    "multi-step",
    "multi step",
    "first ",
    "second ",
    "third ",
    "rows",
    "dataset",
    "dataframe",
    "df.",
    "pd.",
)

_CODE_DEPENDENCY_MARKERS = (
    "import ",
    "read_csv",
    "pd.read",
    "pd.",
    "df =",
    "df=",
    "load(",
    "fit(",
    "train",
)


def is_independent_parallel_runs(expected_edits: dict[int, str]) -> bool:
    """True when every edited cell is a trivial independent print (batch all runs)."""
    if len(expected_edits) < 2:
        return False
    for content in expected_edits.values():
        if not re.match(r"^print\s*\([^)]+\)\s*$", str(content or "").strip(), re.I):
            return False
    return True


def is_sequential_pipeline(user_prompt: str, expected_edits: dict[int, str]) -> bool:
    """True when cells likely depend on prior execution (import → load → print)."""
    if len(expected_edits) < 2:
        return False
    if is_independent_parallel_runs(expected_edits):
        return False

    text = str(user_prompt or "").lower()
    if any(marker in text for marker in _PIPELINE_PROMPT_MARKERS):
        return True

    code_blob = "\n".join(str(v) for v in expected_edits.values()).lower()
    hits = sum(1 for m in _CODE_DEPENDENCY_MARKERS if m in code_blob)
    return hits >= 2 or ("import " in code_blob and len(expected_edits) >= 2)


def init_pipeline_state(
    user_prompt: str,
    run_queue: list[int],
    *,
    sequential: bool = True,
) -> dict[str, Any]:
    ordered = sorted({int(i) for i in run_queue if i is not None})
    return {
        "active": bool(ordered) and sequential,
        "sequential": sequential,
        "user_goal": str(user_prompt or "").strip(),
        "run_queue": ordered,
        "pending_runs": list(ordered),
        "completed_runs": [],
        "last_run_cell": None,
        "last_run_ok": None,
        "complete": not ordered,
    }


def merge_pipeline_state(
    previous: dict[str, Any] | None,
    verification: dict[str, Any],
) -> dict[str, Any] | None:
    if isinstance(verification.get("pipeline"), dict):
        return verification["pipeline"]
    if isinstance(previous, dict) and previous.get("active"):
        return previous
    return None


def _fetch_cell(registry, url: str, cell_index: int, *, include_output: bool) -> dict[str, Any]:
    try:
        return registry.call(
            "notebook_get_cell",
            {
                "url": url,
                "cell_index": int(cell_index),
                "include_output": include_output,
            },
        )
    except Exception as exc:
        return {"ok": False, "cell_index": cell_index, "error": str(exc)}


def build_pipeline_step_context(
    registry,
    url: str,
    pipeline: dict[str, Any],
) -> dict[str, Any]:
    last_ci = pipeline.get("last_run_cell")
    pending = list(pipeline.get("pending_runs") or [])
    next_ci = pending[0] if pending else None

    step: dict[str, Any] = {
        "pending_runs": pending,
        "completed_runs": list(pipeline.get("completed_runs") or []),
        "next_run_cell": next_ci,
    }
    if last_ci is not None:
        step["last_run"] = _fetch_cell(registry, url, int(last_ci), include_output=True)
    if next_ci is not None:
        step["next_cell"] = _fetch_cell(registry, url, int(next_ci), include_output=False)
    return step


def record_pipeline_run(
    pipeline: dict[str, Any],
    cell_index: int,
    run_wait: dict[str, Any],
) -> dict[str, Any]:
    out = dict(pipeline)
    ci = int(cell_index)
    pending = [int(x) for x in (out.get("pending_runs") or []) if x is not None]
    completed = [int(x) for x in (out.get("completed_runs") or []) if x is not None]

    if ci in pending:
        pending.remove(ci)
    if ci not in completed:
        completed.append(ci)

    ok = bool(run_wait.get("ok"))
    succeeded = run_wait.get("run_succeeded")
    if succeeded is None and ok:
        try:
            from .agentic_batch_executor import analyze_cell_output
        except Exception:
            from agentic_batch_executor import analyze_cell_output
        analysis = analyze_cell_output(run_wait.get("output"))
        succeeded = analysis.get("run_succeeded")

    out["pending_runs"] = pending
    out["completed_runs"] = completed
    out["last_run_cell"] = ci
    out["last_run_ok"] = bool(succeeded) if ok else False
    out["complete"] = not pending and bool(succeeded) if ok else False
    out["active"] = not out["complete"] or not ok
    if not ok:
        out["active"] = True
        out["complete"] = False
    return out


def pipeline_user_response_gate(pipeline: dict[str, Any]) -> str:
    pending = pipeline.get("pending_runs") or []
    next_ci = pending[0] if pending else None
    last_ci = pipeline.get("last_run_cell")

    if pipeline.get("complete"):
        return (
            "Pipeline complete. Write a final Summary for the user using all run outputs "
            "from pipeline_step / post_run_query. Do not ask the user to run cells manually."
        )

    if last_ci is None and pending:
        return (
            f"Pipeline write phase done. Emit run_cell({next_ci}) to start sequential execution. "
            "Do not give manual instructions — use tools."
        )

    if pipeline.get("last_run_ok") is False:
        return (
            f"Last run (cell {last_ci}) failed. Read pipeline_step.last_run, fix with "
            f"edit_cell_by_index({last_ci}) + run_cell({last_ci}). "
            "Do not tell the user to fix it themselves."
        )

    if next_ci is not None:
        return (
            f"Pipeline step ready. In ONE turn: read pipeline_step (last_run output + next_cell code), "
            f"verify against user_goal, then emit run_cell({next_ci}) if requirements are met. "
            "Do not reply with instructions-only text. Do not skip remaining pending_runs."
        )

    return "Continue the pipeline with tools until pipeline.complete is true."


def attach_pipeline_to_verification(
    verification: dict[str, Any],
    *,
    registry,
    url: str,
    pipeline: dict[str, Any] | None,
) -> dict[str, Any]:
    if not pipeline or not pipeline.get("active") and not pipeline.get("complete"):
        return verification

    verification["pipeline"] = pipeline
    verification["pipeline_step"] = build_pipeline_step_context(registry, url, pipeline)
    verification["user_response_gate"] = pipeline_user_response_gate(pipeline)
    verification["await_llm_summary"] = bool(pipeline.get("complete"))
    verification["batch_executed"] = True

    if pipeline.get("complete"):
        verification["pipeline_complete"] = True
    elif pipeline.get("pending_runs"):
        verification["pipeline_active"] = True

    return verification


def strip_runs_for_write_phase(
    calls: list,
    *,
    sequential: bool,
) -> tuple[list, list]:
    """Move run_cell calls to deferred when entering sequential write-first phase."""
    if not sequential:
        return calls, []
    kept: list = []
    deferred: list = []
    for call in calls:
        if call.name == "run_cell":
            deferred.append(call)
        else:
            kept.append(call)
    return kept, deferred
