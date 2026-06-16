#!/usr/bin/env python3
"""Run FYP agent evaluation tests (1–4) and write per-test summary + report."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = Path(__file__).resolve().parent
_HOST_DIR = _SCRIPTS_DIR.parent
_LOG_DIR = _HOST_DIR / "data" / "logs"
_NOTEBOOKS_DIR = _HOST_DIR / "data" / "notebooks"
_BENCHMARKS_PATH = _SCRIPTS_DIR / "fyp_experiment_benchmarks_agent_tests.json"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from testing.host.scripts.fyp_experiment_runner import (  # noqa: E402
    READ_TOOLS,
    WRITE_TOOLS,
    _read_jsonl_from_offset,
    _read_jsonl,
    _snapshot_offsets,
    _TOKEN_PATH,
    _TRACE_PATH,
    _utc_now,
    collect_metrics_from_logs,
    load_benchmarks,
    run_harness_case,
)

WRITE_ONLY = frozenset(
    t
    for t in WRITE_TOOLS
    if t not in ("run_cell", "run_cell_by_index")
)


def _ensure_live_snapshot(snapshot_file: str) -> None:
    name = str(snapshot_file or "").strip()
    if not name:
        return
    persistent = _NOTEBOOKS_DIR / "persistent" / name
    live = _NOTEBOOKS_DIR / "live" / name
    if not persistent.is_file():
        raise FileNotFoundError(f"Persistent snapshot not found: {persistent}")
    live.parent.mkdir(parents=True, exist_ok=True)
    if not live.is_file() or persistent.stat().st_mtime > live.stat().st_mtime:
        shutil.copy2(persistent, live)


def _tool_name(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _trace_rows_for_session(trace_rows: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    """Isolate trace events belonging to one chat session."""
    sid = str(session_id or "").strip()
    if not sid or not trace_rows:
        return list(trace_rows)
    start_idx = None
    end_idx = len(trace_rows)
    for i, row in enumerate(trace_rows):
        if row.get("event") != "session_start":
            continue
        row_sid = str(row.get("session_id") or "")
        if row_sid == sid and start_idx is None:
            start_idx = i
        elif start_idx is not None and row_sid and row_sid != sid:
            end_idx = i
            break
    if start_idx is None:
        return [r for r in trace_rows if str(r.get("session_id") or "") in ("", sid)]
    return trace_rows[start_idx:end_idx]


def analyze_trace(
    trace_rows: list[dict[str, Any]],
    *,
    target_cell: int | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """Derive agent-test-specific metrics from tool trace events."""
    trace_rows = _trace_rows_for_session(trace_rows, session_id)
    out: dict[str, Any] = {
        "insert_calls": 0,
        "edit_calls": 0,
        "delete_calls": 0,
        "run_calls": 0,
        "read_calls": 0,
        "verify_events": [],
        "reorder_events": [],
        "run_errors": [],
        "batch_tool_orders": [],
        "final_verification_status": "unknown",
        "target_cell_modified": False,
        "target_cell_executed": False,
        "target_cell_verified": False,
        "execution_evidence": "",
        "batch_writes_before_runs": None,
    }

    last_verify: dict[str, Any] | None = None
    for row in trace_rows:
        event = str(row.get("event") or "").lower()
        tool = _tool_name(row.get("tool"))

        if event == "verify":
            v = {
                "round": row.get("round"),
                "verified": row.get("verified"),
                "goal_verified": row.get("goal_verified"),
                "queue_status": row.get("queue_status"),
                "executed": row.get("executed") or [],
            }
            out["verify_events"].append(v)
            last_verify = v

        if event == "reorder":
            out["reorder_events"].append(
                {"round": row.get("round"), "before": row.get("before"), "after": row.get("after")}
            )

        if event == "batch_start":
            tools = [_tool_name(t) for t in (row.get("tools") or [])]
            out["batch_tool_orders"].append({"round": row.get("round"), "tools": tools})
            if tools:
                last_write = -1
                first_run = len(tools)
                for i, t in enumerate(tools):
                    if t in WRITE_ONLY or t == "insert_cell" or t == "edit_cell_by_index":
                        last_write = i
                    if t in ("run_cell", "run_cell_by_index") and first_run == len(tools):
                        first_run = i
                out["batch_writes_before_runs"] = last_write < first_run if any(
                    t in ("run_cell", "run_cell_by_index") for t in tools
                ) else None

        if event in ("dispatch", "exec", "verify"):
            executed = row.get("executed") if event == "verify" else None
            items = executed if isinstance(executed, list) else [{"tool": tool, "args": row.get("args") or {}}]
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = _tool_name(item.get("tool"))
                args = item.get("args") if isinstance(item.get("args"), dict) else row.get("args") or {}
                if t == "insert_cell":
                    out["insert_calls"] += 1
                elif t in ("edit_cell_by_index", "edit_cell"):
                    out["edit_calls"] += 1
                    ci = args.get("cell_index") or args.get("index")
                    if target_cell is not None and str(ci) == str(target_cell):
                        out["target_cell_modified"] = True
                elif t == "delete_by_index":
                    out["delete_calls"] += 1
                elif t in ("run_cell", "run_cell_by_index"):
                    out["run_calls"] += 1
                    ci = args.get("cell_index") or args.get("index")
                    if target_cell is not None and str(ci) == str(target_cell):
                        out["target_cell_executed"] = True
                elif t in READ_TOOLS:
                    out["read_calls"] += 1

        if event in ("run_error", "RUN_ERROR"):
            out["run_errors"].append(
                {
                    "round": row.get("round"),
                    "cell": row.get("failed_cell_index"),
                    "preview": row.get("error_preview") or row.get("error"),
                }
            )

    if last_verify:
        verified = last_verify.get("verified")
        queue = last_verify.get("queue_status")
        if verified is True and queue in ("dispatched", "complete", None):
            out["final_verification_status"] = "verified"
        elif verified is False:
            out["final_verification_status"] = "failed"
        else:
            out["final_verification_status"] = str(queue or "partial")
        executed = last_verify.get("executed") or []
        if executed:
            out["execution_evidence"] = json.dumps(executed[:12], ensure_ascii=False, default=str)
        if target_cell is not None:
            for ex in executed:
                if not isinstance(ex, dict):
                    continue
                if _tool_name(ex.get("tool")) in ("run_cell", "edit_cell_by_index", "edit_cell"):
                    ci = ex.get("cell_index")
                    if str(ci) == str(target_cell) and ex.get("dispatched"):
                        out["target_cell_verified"] = True

    return out


def _evaluate_test_pass(case: dict[str, Any], metrics: dict[str, Any], trace: dict[str, Any]) -> tuple[str, bool, str]:
    """Return (completion_status, passed, rationale) using test-specific criteria."""
    tn = int(case.get("test_number") or 0)
    tool_calls = int(metrics.get("total_tool_calls") or 0)
    repair = int(metrics.get("repair_rounds") or 0)
    verify = str(metrics.get("final_verification_status") or "unknown")

    if tn == 1:
        inserts = int(trace.get("insert_calls") or 0)
        edits = int(trace.get("edit_calls") or 0)
        runs = int(trace.get("run_calls") or 0)
        ok = tool_calls >= 3 and inserts + edits >= 1 and runs >= 1 and verify == "verified"
        if ok and tool_calls < 10:
            return "partial", False, f"Pipeline tools dispatched ({tool_calls}) but full 8-step pipeline not evidenced in one 2-call turn."
        return ("success" if ok else "partial"), ok, (
            "ML pipeline cells created, edited, and run with verification."
            if ok
            else "Insufficient tool coverage for full pipeline in bounded agentic turn."
        )

    if tn == 2:
        edits = int(trace.get("edit_calls") or 0)
        runs = int(trace.get("run_calls") or 0)
        ok = edits >= 1 and runs >= 1 and verify == "verified"
        return ("success" if ok else "partial"), ok, (
            "At least one in-place edit and run dispatched."
            if ok
            else "Agent did not dispatch edit+run repairs for notebook errors."
        )

    if tn == 3:
        inserts = int(trace.get("insert_calls") or 0)
        ok = inserts >= 10 and verify == "verified"
        bwr = trace.get("batch_writes_before_runs")
        note = "Batch ordering OK." if bwr else "Batch ordering not observed."
        return ("success" if ok else "failed"), ok, (
            f"Created {inserts}/10 cells. {note}"
        )

    if tn == 4:
        ok = bool(metrics.get("verification_attack_passed"))
        return ("success" if ok else "failed"), ok, (
            "Cell 31 modified, executed, and verification evidence present."
            if ok
            else "Success not verified — cell 31 fix/execute evidence missing (anti-hallucination check)."
        )

    base = str(metrics.get("completion_status") or "unknown")
    return base, base == "success", "Default harness status."


def enrich_run_metrics(run: dict[str, Any], trace_rows: list[dict[str, Any]], case: dict[str, Any]) -> dict[str, Any]:
    target = case.get("target_cell")
    trace = analyze_trace(
        trace_rows,
        target_cell=int(target) if target is not None else None,
        session_id=str(run.get("session_id") or ""),
    )
    metrics = dict(run.get("metrics") or {})
    metrics["agent_trace"] = trace
    metrics["final_verification_status"] = trace.get("final_verification_status", "unknown")

    if case.get("test_number") == 4:
        ok = (
            trace.get("target_cell_modified")
            and trace.get("target_cell_executed")
            and trace.get("target_cell_verified")
        )
        metrics["verification_attack_passed"] = bool(ok)

    status, passed, rationale = _evaluate_test_pass(case, metrics, trace)
    metrics["completion_status"] = status
    metrics["test_passed"] = passed
    metrics["evaluation_rationale"] = rationale

    run["metrics"] = metrics
    run["test_number"] = case.get("test_number")
    run["test_name"] = case.get("test_name")
    run["tests_evaluated"] = case.get("tests") or []
    run["target_cell"] = case.get("target_cell")
    return run


def _output_stem(test_id: str, kernel_id: int | str) -> str:
    return f"{test_id}_kaggle_kernel_{kernel_id}"


def generate_agent_test_summary(payload: dict[str, Any], run: dict[str, Any]) -> str:
    m = run.get("metrics") or {}
    trace = m.get("agent_trace") or {}
    tn = run.get("test_number") or ""
    name = run.get("test_name") or run.get("id") or ""
    kernel_id = run.get("kernel_id") or ""
    status = m.get("completion_status", "unknown")
    passed = bool(m.get("test_passed"))
    rationale = str(m.get("evaluation_rationale") or "")

    lines = [
        f"# Agent Test {tn} — {name} (kernel {kernel_id})",
        "",
        "## Notebook",
        "",
        f"- **Test ID:** `{run.get('id', '')}`",
        f"- **Kernel ID:** `{kernel_id}`",
        f"- **URL:** {run.get('url', '')}",
        f"- **Snapshot:** `persistent/{run.get('snapshot_file', '')}`",
        f"- **Live LLM:** {payload.get('live_llm', False)}",
        f"- **Evaluates:** {', '.join(run.get('tests_evaluated') or [])}",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Completion | {'PASS' if passed else 'FAIL'} ({status}) |",
        f"| LLM calls | {m.get('total_llm_calls', 0)} |",
        f"| Tool calls | {m.get('total_tool_calls', 0)} |",
        f"| Repair rounds | {m.get('repair_rounds', 0)} |",
        f"| Runtime errors | {m.get('runtime_errors', 0)} |",
        f"| Execution time (s) | {m.get('execution_time', 0)} |",
        f"| Final verification | {m.get('final_verification_status', 'unknown')} |",
    ]

    if tn == 1:
        lines.extend(
            [
                f"| Insert / edit / run tools | {trace.get('insert_calls', 0)} / {trace.get('edit_calls', 0)} / {trace.get('run_calls', 0)} |",
            ]
        )
    elif tn == 2:
        lines.append(f"| Run errors in trace | {len(trace.get('run_errors') or [])} |")
    elif tn == 3:
        bwr = trace.get("batch_writes_before_runs")
        lines.append(f"| Batch writes-before-runs | {bwr if bwr is not None else 'n/a'} |")
        lines.append(f"| Reorder events | {len(trace.get('reorder_events') or [])} |")
    elif tn == 4:
        lines.extend(
            [
                f"| Cell 31 modified | {trace.get('target_cell_modified', False)} |",
                f"| Cell 31 executed | {trace.get('target_cell_executed', False)} |",
                f"| Cell 31 verified | {trace.get('target_cell_verified', False)} |",
                f"| Verification attack passed | {m.get('verification_attack_passed', False)} |",
            ]
        )

    if rationale:
        lines.extend(["", "## Evaluation", "", rationale, ""])

    lines.extend(["", f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def generate_agent_test_report(payload: dict[str, Any], run: dict[str, Any]) -> str:
    m = run.get("metrics") or {}
    trace = m.get("agent_trace") or {}
    tn = run.get("test_number") or ""
    name = run.get("test_name") or ""
    prompt = str(run.get("prompt") or "").strip()

    lines = [
        f"# Agent Test {tn} — {name} — Full Report",
        "",
        "## Overview",
        "",
        f"- **Test ID:** `{run.get('id', '')}`",
        f"- **Agent:** {payload.get('agent', 'Notebook Agent v1')}",
        f"- **Kernel:** `{run.get('kernel_id', '')}`",
        f"- **Notebook key:** `{run.get('notebook_key', '')}`",
        f"- **Session:** `{run.get('session_id', '')}`",
        f"- **Started:** {payload.get('started_at', '')}",
        f"- **Finished:** {payload.get('finished_at', '')}",
        "",
        "## Prompt",
        "",
        "```",
        prompt,
        "```",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Tool calls | {m.get('total_tool_calls', 0)} |",
        f"| Repair rounds | {m.get('repair_rounds', 0)} |",
        f"| Execution time (s) | {m.get('execution_time', 0)} |",
        f"| Final verification status | {m.get('final_verification_status', 'unknown')} |",
        f"| Completion status | {m.get('completion_status', 'unknown')} |",
        "",
        "## Tool breakdown",
        "",
        f"- Reads: {m.get('notebook_reads', 0)}",
        f"- Writes: {m.get('notebook_writes', 0)}",
        f"- Inserts: {trace.get('insert_calls', 0)}",
        f"- Edits: {trace.get('edit_calls', 0)}",
        f"- Deletes: {trace.get('delete_calls', 0)}",
        f"- Runs: {trace.get('run_calls', 0)}",
        "",
    ]

    if tn == 3 and trace.get("batch_tool_orders"):
        lines.append("## Batch tool order")
        lines.append("")
        for batch in trace["batch_tool_orders"]:
            tools = batch.get("tools") or []
            lines.append(f"- Round {batch.get('round')}: {', '.join(tools)}")
        lines.append("")

    if tn == 4:
        lines.extend(
            [
                "## Verification attack checklist",
                "",
                f"1. Cell {run.get('target_cell', 31)} modified: **{trace.get('target_cell_modified', False)}**",
                f"2. Cell {run.get('target_cell', 31)} executed: **{trace.get('target_cell_executed', False)}**",
                f"3. Verification confirmed: **{trace.get('target_cell_verified', False)}**",
                f"4. Execution evidence: `{trace.get('execution_evidence', '')[:500]}`",
                "",
                f"**Overall:** {'PASS — evidence present' if m.get('verification_attack_passed') else 'FAIL — success not verified'}",
                "",
            ]
        )

    if trace.get("verify_events"):
        lines.append("## Verification events")
        lines.append("")
        for v in trace["verify_events"]:
            lines.append(
                f"- Round {v.get('round')}: verified={v.get('verified')} "
                f"queue={v.get('queue_status')} executed={len(v.get('executed') or [])}"
            )
        lines.append("")

    if run.get("error"):
        lines.extend([f"## Error\n\n`{run['error']}`\n"])

    lines.extend([f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def write_agent_test_outputs(payload: dict[str, Any], run: dict[str, Any]) -> dict[str, str]:
    test_id = str(run.get("id") or "AGENT_TEST")
    kernel_id = run.get("kernel_id") or ""
    stem = _output_stem(test_id, kernel_id)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    results_path = _LOG_DIR / f"{stem}_results.json"
    summary_path = _LOG_DIR / f"{stem}_summary.md"
    report_path = _LOG_DIR / f"{stem}_report.md"

    results_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary_path.write_text(generate_agent_test_summary(payload, run), encoding="utf-8")
    report_path.write_text(generate_agent_test_report(payload, run), encoding="utf-8")

    return {
        "results_path": str(results_path),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
    }


def run_agent_test_case(case: dict[str, Any], defaults: dict[str, Any], *, live_llm: bool) -> dict[str, Any]:
    snapshot_file = str(case.get("snapshot_file") or "")
    _ensure_live_snapshot(snapshot_file)

    test_id = str(case.get("id") or "")
    kernel_id = case.get("kernel_id")
    experiment_id = f"{test_id}_{kernel_id}"

    offsets = _snapshot_offsets()
    run_result = run_harness_case(case, defaults, experiment_id=experiment_id, live_llm=live_llm)

    trace_rows = _read_jsonl_from_offset(_TRACE_PATH, offsets.trace)
    token_rows = _read_jsonl_from_offset(_TOKEN_PATH, offsets.token)
    session_id = run_result.get("session_id", "")

    metrics = collect_metrics_from_logs(
        trace_rows=trace_rows,
        token_rows=token_rows,
        session_id=session_id,
        notebook_key=str(case.get("notebook_key") or ""),
        execution_time=float((run_result.get("metrics") or {}).get("execution_time") or 0),
        error_text=str(run_result.get("error") or ""),
        llm_call_count=(run_result.get("metrics") or {}).get("total_llm_calls"),
    )
    run_result["metrics"] = asdict(metrics)
    run_result = enrich_run_metrics(run_result, trace_rows, case)

    payload = {
        "agent": "Notebook Agent v1",
        "experiment_id": experiment_id,
        "test_id": test_id,
        "test_number": case.get("test_number"),
        "test_name": case.get("test_name"),
        "mode": "harness",
        "live_llm": live_llm,
        "started_at": _utc_now(),
        "finished_at": _utc_now(),
        "benchmarks_file": str(_BENCHMARKS_PATH),
        "runs": [run_result],
        "aggregate": {
            "run_count": 1,
            "completion_status_counts": {run_result["metrics"].get("completion_status", "unknown"): 1},
        },
    }
    paths = write_agent_test_outputs(payload, run_result)
    return {"test_id": test_id, "kernel_id": kernel_id, **paths}


def regenerate_agent_test_docs() -> list[dict[str, Any]]:
    bench = load_benchmarks(_BENCHMARKS_PATH)
    case_by_id = {str(c.get("id")): c for c in (bench.get("prompts") or [])}
    outputs = []
    for results_path in sorted(_LOG_DIR.glob("AGENT_TEST_*_results.json")):
        payload = json.loads(results_path.read_text(encoding="utf-8"))
        run = (payload.get("runs") or [{}])[0]
        case = case_by_id.get(str(run.get("id") or ""), {})
        trace_rows = _read_jsonl(_TRACE_PATH)
        run = enrich_run_metrics(run, _trace_rows_for_session(trace_rows, str(run.get("session_id") or "")), case)
        payload["runs"] = [run]
        paths = write_agent_test_outputs(payload, run)
        outputs.append(paths)
        print(f"Regenerated: {paths['summary_path']}")
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run FYP agent tests 1–4")
    parser.add_argument("--live-llm", action="store_true", help="Use real Cerebras API")
    parser.add_argument("--only", nargs="+", metavar="ID", help="Run specific test ids")
    parser.add_argument("--regenerate", action="store_true", help="Rebuild docs from results JSON")
    args = parser.parse_args(argv)

    if args.regenerate:
        outputs = regenerate_agent_test_docs()
        print(json.dumps(outputs, indent=2))
        return 0

    bench = load_benchmarks(_BENCHMARKS_PATH)
    defaults = bench.get("defaults") or {}
    only = set(args.only) if args.only else None
    outputs = []

    for case in bench.get("prompts") or []:
        if only and str(case.get("id") or "") not in only:
            continue
        print(f"Running {case.get('id')} on kernel {case.get('kernel_id')}...")
        out = run_agent_test_case(case, defaults, live_llm=bool(args.live_llm))
        outputs.append(out)
        print(f"  summary: {out['summary_path']}")
        print(f"  report:  {out['report_path']}")

    print(json.dumps(outputs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
