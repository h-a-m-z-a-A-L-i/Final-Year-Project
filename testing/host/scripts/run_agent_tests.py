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
    """Derive agent-test metrics from LLM-dispatched tool batches (fire-and-forget)."""
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
        "dispatched_tool_list": [],
        "target_cell_edited": False,
        "target_cell_run": False,
        "batch_writes_before_runs": None,
        "evaluation_mode": "llm_dispatch_only",
    }

    seen_dispatches: set[str] = set()

    def _bump_count(tool: str, args: dict | None = None) -> None:
        t = _tool_name(tool)
        if not t:
            return
        out["dispatched_tool_list"].append(t)
        args = args or {}
        ci = args.get("cell_index") or args.get("index")
        if t == "insert_cell":
            out["insert_calls"] += 1
        elif t in ("edit_cell_by_index", "edit_cell"):
            out["edit_calls"] += 1
            if target_cell is not None and str(ci) == str(target_cell):
                out["target_cell_edited"] = True
        elif t == "delete_by_index":
            out["delete_calls"] += 1
        elif t in ("run_cell", "run_cell_by_index"):
            out["run_calls"] += 1
            if target_cell is not None and str(ci) == str(target_cell):
                out["target_cell_run"] = True
        elif t in READ_TOOLS:
            out["read_calls"] += 1

    def _record_dispatch(tool: str, args: dict | None = None) -> None:
        t = _tool_name(tool)
        if not t:
            return
        key = f"{t}:{json.dumps(args or {}, sort_keys=True, default=str)}"
        if key in seen_dispatches:
            return
        seen_dispatches.add(key)
        _bump_count(t, args)

    for row in trace_rows:
        event = str(row.get("event") or "").lower()
        tool = _tool_name(row.get("tool"))

        if event == "verify":
            out["verify_events"].append(
                {
                    "round": row.get("round"),
                    "queue_status": row.get("queue_status"),
                    "executed": row.get("executed") or [],
                }
            )
            for item in row.get("executed") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("dispatched") is False:
                    continue
                t = _tool_name(item.get("tool"))
                args = item.get("args") if isinstance(item.get("args"), dict) else {}
                if item.get("cell_index") is not None:
                    args = {**args, "cell_index": item.get("cell_index")}
                _bump_count(t, args)

        if event == "reorder":
            out["reorder_events"].append(
                {"round": row.get("round"), "before": row.get("before"), "after": row.get("after")}
            )

        if event == "batch_start":
            tools = [_tool_name(t) for t in (row.get("tools") or [])]
            out["batch_tool_orders"].append({"round": row.get("round"), "tools": tools})
            for t in tools:
                _bump_count(t)
            if tools:
                last_write = -1
                first_run = len(tools)
                for i, t in enumerate(tools):
                    if t in WRITE_ONLY or t in ("insert_cell", "edit_cell_by_index", "edit_cell"):
                        last_write = i
                    if t in ("run_cell", "run_cell_by_index") and first_run == len(tools):
                        first_run = i
                out["batch_writes_before_runs"] = last_write < first_run if any(
                    t in ("run_cell", "run_cell_by_index") for t in tools
                ) else None

        if event == "dispatch":
            args = row.get("args") if isinstance(row.get("args"), dict) else {}
            _record_dispatch(tool, args)

        if event in ("run_error", "RUN_ERROR"):
            out["run_errors"].append(
                {
                    "round": row.get("round"),
                    "cell": row.get("failed_cell_index"),
                    "preview": row.get("error_preview") or row.get("error"),
                }
            )

    return out


def _evaluate_test_pass(case: dict[str, Any], metrics: dict[str, Any], trace: dict[str, Any]) -> tuple[str, bool, str]:
    """Evaluate LLM tool-call dispatch only (fire-and-forget). No ReAct verification loop."""
    tn = int(case.get("test_number") or 0)
    tool_calls = int(metrics.get("total_tool_calls") or 0)
    inserts = int(trace.get("insert_calls") or 0)
    edits = int(trace.get("edit_calls") or 0)
    runs = int(trace.get("run_calls") or 0)
    reads = int(trace.get("read_calls") or 0)
    writes = inserts + edits + int(trace.get("delete_calls") or 0)

    if tn == 1:
        ok = tool_calls >= 2 and writes >= 1 and runs >= 1 and reads >= 1
        partial = ok and tool_calls < 6
        return (
            ("partial" if partial else "success") if ok else "failed",
            ok,
            (
                f"LLM dispatched {tool_calls} tool(s): {inserts} insert, {edits} edit, {runs} run, {reads} read."
                if ok
                else f"Insufficient dispatch — tools={tool_calls}, writes={writes}, runs={runs}, reads={reads}."
            ),
        )

    if tn == 2:
        ok = edits >= 1 and reads >= 1
        status = "success" if ok and runs >= 1 else ("partial" if ok else "failed")
        return (
            status,
            ok,
            (
                f"LLM dispatched in-place edit ({edits}) and run ({runs}) with {reads} read(s)."
                if ok
                else "No in-place edit dispatched for error repair."
            ),
        )

    if tn == 3:
        bwr = trace.get("batch_writes_before_runs")
        ok = inserts >= 10
        partial = inserts >= 3
        if ok:
            msg = f"LLM dispatched {inserts}/10 insert_cell calls."
        elif partial:
            msg = f"LLM dispatched {inserts}/10 inserts (partial batch)."
        else:
            msg = f"Only {inserts} insert_cell dispatch(es); expected 10."
        if bwr is True:
            msg += " Writes-before-runs ordering observed."
        return ("success" if ok else ("partial" if partial else "failed"), ok or partial, msg)

    if tn == 4:
        target = int(case.get("target_cell") or 31)
        edited = bool(trace.get("target_cell_edited"))
        run_tgt = bool(trace.get("target_cell_run"))
        ok = edited and run_tgt
        partial = edited or run_tgt
        return (
            "success" if ok else ("partial" if partial else "failed"),
            ok,
            (
                f"LLM dispatched edit+run for cell {target}."
                if ok
                else (
                    f"Partial dispatch for cell {target}: edit={edited}, run={run_tgt}."
                    if partial
                    else f"No edit/run dispatch targeting cell {target}."
                )
            ),
        )

    base = str(metrics.get("completion_status") or "unknown")
    return base, base in ("success", "partial"), "Default harness status."


def enrich_run_metrics(run: dict[str, Any], trace_rows: list[dict[str, Any]], case: dict[str, Any]) -> dict[str, Any]:
    target = case.get("target_cell")
    trace = analyze_trace(
        trace_rows,
        target_cell=int(target) if target is not None else None,
        session_id=str(run.get("session_id") or ""),
    )
    metrics = dict(run.get("metrics") or {})
    metrics["agent_trace"] = trace
    metrics["evaluation_mode"] = "llm_dispatch_only"

    status, passed_strict, rationale = _evaluate_test_pass(case, metrics, trace)
    passed = status in ("success", "partial")
    metrics["completion_status"] = status
    metrics["test_passed"] = passed
    metrics["tool_dispatch_passed"] = passed_strict if status == "success" else passed
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
        f"- **Evaluation:** LLM tool dispatch only (fire-and-forget; no ReAct verification loop)",
        f"- **Evaluates:** {', '.join(run.get('tests_evaluated') or [])}",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Dispatch eval | {'PASS' if passed else 'FAIL'} ({status}) |",
        f"| LLM calls | {m.get('total_llm_calls', 0)} |",
        f"| Tool calls (dispatched) | {m.get('total_tool_calls', 0)} |",
        f"| Repair rounds | {m.get('repair_rounds', 0)} |",
        f"| Runtime errors | {m.get('runtime_errors', 0)} |",
        f"| Execution time (s) | {m.get('execution_time', 0)} |",
        f"| Insert / edit / run / read | {trace.get('insert_calls', 0)} / {trace.get('edit_calls', 0)} / {trace.get('run_calls', 0)} / {trace.get('read_calls', 0)} |",
    ]

    if tn == 3:
        bwr = trace.get("batch_writes_before_runs")
        lines.append(f"| Batch writes-before-runs | {bwr if bwr is not None else 'n/a'} |")
    elif tn == 4:
        tc = run.get("target_cell") or 31
        lines.extend(
            [
                f"| Cell {tc} edit dispatched | {trace.get('target_cell_edited', False)} |",
                f"| Cell {tc} run dispatched | {trace.get('target_cell_run', False)} |",
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
        f"| Tool calls (dispatched) | {m.get('total_tool_calls', 0)} |",
        f"| Repair rounds | {m.get('repair_rounds', 0)} |",
        f"| Execution time (s) | {m.get('execution_time', 0)} |",
        f"| Dispatch status | {m.get('completion_status', 'unknown')} |",
        f"| Evaluation mode | {trace.get('evaluation_mode', 'llm_dispatch_only')} |",
        "",
        "## Dispatched tool breakdown",
        "",
        f"- Reads: {trace.get('read_calls', 0)}",
        f"- Inserts: {trace.get('insert_calls', 0)}",
        f"- Edits: {trace.get('edit_calls', 0)}",
        f"- Deletes: {trace.get('delete_calls', 0)}",
        f"- Runs: {trace.get('run_calls', 0)}",
        f"- Tool sequence: {', '.join(trace.get('dispatched_tool_list') or [])}",
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
        tc = run.get("target_cell") or 31
        lines.extend(
            [
                "## Cell-target dispatch checklist",
                "",
                f"1. Cell {tc} edit dispatched: **{trace.get('target_cell_edited', False)}**",
                f"2. Cell {tc} run dispatched: **{trace.get('target_cell_run', False)}**",
                "",
                f"**Overall:** {'PASS' if trace.get('target_cell_edited') and trace.get('target_cell_run') else 'PARTIAL/FAIL'}",
                "",
            ]
        )

    if trace.get("verify_events"):
        lines.append("## Dispatch batches (fire-and-forget)")
        lines.append("")
        for v in trace["verify_events"]:
            executed = v.get("executed") or []
            tools = [str(x.get("tool")) for x in executed if isinstance(x, dict)]
            lines.append(f"- Round {v.get('round')}: queue={v.get('queue_status')} tools={', '.join(tools)}")
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
