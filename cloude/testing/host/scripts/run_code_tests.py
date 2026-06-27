#!/usr/bin/env python3
"""Run FYP Code-mode evaluation tests (1–4) and write per-test summary + report."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = Path(__file__).resolve().parent
_HOST_DIR = _SCRIPTS_DIR.parent
_LOG_DIR = _HOST_DIR / "data" / "logs"
_NOTEBOOKS_DIR = _HOST_DIR / "data" / "notebooks"
_BENCHMARKS_PATH = _SCRIPTS_DIR / "fyp_experiment_benchmarks_code.json"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from testing.host.scripts.fyp_experiment_runner import (  # noqa: E402
    _utc_now,
    load_benchmarks,
    run_harness_case,
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


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _topic_hits(text: str, topics: list[str]) -> tuple[int, list[str], list[str]]:
    norm = _norm(text)
    hit, miss = [], []
    for topic in topics:
        if _norm(topic) in norm:
            hit.append(topic)
        else:
            miss.append(topic)
    return len(hit), hit, miss


def _python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)


def _has_placement(text: str) -> bool:
    norm = _norm(text)
    return bool(
        re.search(r"\bplacement\b", norm)
        or re.search(r"\b(below|after)\s+cell\s+\d+", norm)
        or re.search(r"\bfill\s+cell\s+\d+", norm)
        or re.search(r"\binsert\b.{0,40}\bcell\b", norm)
    )


def _cited_cells(text: str) -> list[int]:
    return sorted({int(m) for m in re.findall(r"\bcell\s*\[?(\d+)\]?", _norm(text), flags=re.I)})


def _placement_accuracy(case: dict[str, Any], text: str) -> float | None:
    tn = int(case.get("test_number") or 0)
    cited = _cited_cells(text)
    if tn == 4:
        return None
    target = case.get("target_cell")
    if target is not None:
        return 1.0 if int(target) in cited else (0.5 if cited else 0.0)
    expected = [int(x) for x in (case.get("expected_placement_cells") or [])]
    if not expected or not cited:
        return 0.0 if tn != 4 else None
    hits = sum(1 for c in cited if c in expected)
    if hits:
        return round(min(1.0, hits / max(1, len(cited))), 3)
    nearest = min(min(abs(c - e) for e in expected) for c in cited)
    return round(max(0.0, 1.0 - nearest / 20), 3)


def _code_correctness(case: dict[str, Any], blocks: list[str]) -> float | None:
    tn = int(case.get("test_number") or 0)
    if tn == 4:
        return None
    terms = list(case.get("code_terms") or [])
    if not terms or not blocks:
        return 0.0 if tn != 4 else None
    code = _norm("\n".join(blocks))
    hits = sum(1 for t in terms if _norm(t) in code)
    return round(hits / len(terms), 3)


def evaluate_code_response(
    case: dict[str, Any],
    response_text: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    topics = list(case.get("coverage_topics") or [])
    ground_truth = list(case.get("ground_truth_terms") or [])
    tn = int(case.get("test_number") or 0)

    tool_calls = int(metrics.get("total_tool_calls") or 0)
    writes = int(metrics.get("notebook_writes") or 0)
    blocks = _python_blocks(response_text)
    block_lines = max((len(b.splitlines()) for b in blocks), default=0)

    coverage_hits, covered, missing = _topic_hits(response_text, topics)
    coverage = round(coverage_hits / len(topics), 3) if topics else None

    gt_hit = sum(1 for t in ground_truth if _norm(t) in _norm(response_text))
    downstream_preserved = round(gt_hit / max(1, len(ground_truth)), 3) if ground_truth else None

    placement_accuracy = _placement_accuracy(case, response_text)
    code_correctness = _code_correctness(case, blocks)

    contract = {
        "no_browser_tool_dispatch": tool_calls == 0,
        "no_notebook_writes": writes == 0,
        "has_placement_guidance": _has_placement(response_text),
        "has_python_code_block": bool(blocks),
        "single_primary_code_block": len(blocks) <= 1,
        "has_run_order": bool(re.search(r"\brun\b", _norm(response_text))),
        "acknowledges_empty_cell": True,
        "defers_code_for_empty_cell": True,
        "asks_clarifying_questions": "?" in response_text,
    }

    if tn == 4:
        norm = _norm(response_text)
        contract["acknowledges_empty_cell"] = any(
            k in norm for k in ("empty", "blank", "no code", "no content", "cell 50")
        )
        contract["defers_code_for_empty_cell"] = not blocks or block_lines < 5
        contract["asks_clarifying_questions"] = "?" in response_text or any(
            k in norm for k in ("what would you", "what should", "clarif", "would you like", "tell me")
        )

    contract_ok = contract["no_browser_tool_dispatch"] and contract["no_notebook_writes"]
    if tn == 4:
        contract_ok = (
            contract_ok
            and contract["acknowledges_empty_cell"]
            and contract["defers_code_for_empty_cell"]
            and contract["asks_clarifying_questions"]
        )
    else:
        contract_ok = (
            contract_ok
            and contract["has_python_code_block"]
            and contract["has_placement_guidance"]
            and (contract["single_primary_code_block"] or tn == 2)
        )
        if tn in (1, 3):
            contract_ok = contract_ok and contract["has_run_order"]

    passed = contract_ok
    if tn != 4:
        if placement_accuracy is not None:
            passed = passed and placement_accuracy >= 0.5
        if code_correctness is not None:
            passed = passed and code_correctness >= 0.4
    if downstream_preserved is not None and tn == 2:
        passed = passed and downstream_preserved >= 0.5

    rationale_parts = []
    if not contract["no_browser_tool_dispatch"]:
        rationale_parts.append(f"Dispatched {tool_calls} browser tool call(s).")
    if not contract["no_notebook_writes"]:
        rationale_parts.append("Notebook writes detected.")
    if tn == 4:
        if not contract["acknowledges_empty_cell"]:
            rationale_parts.append("Did not acknowledge empty cell 50.")
        if not contract["defers_code_for_empty_cell"]:
            rationale_parts.append("Generated code before clarifying intent.")
        if not contract["asks_clarifying_questions"]:
            rationale_parts.append("Did not ask clarifying questions.")
    else:
        if not contract["has_python_code_block"]:
            rationale_parts.append("Missing runnable python block.")
        if not contract["has_placement_guidance"]:
            rationale_parts.append("Missing placement guidance.")
        if placement_accuracy is not None and placement_accuracy < 0.5:
            rationale_parts.append(f"Low placement accuracy ({placement_accuracy:.0%}).")
        if code_correctness is not None and code_correctness < 0.4:
            rationale_parts.append(f"Low code correctness ({code_correctness:.0%}).")
    if not rationale_parts:
        rationale_parts.append("Code-mode contract satisfied; placement and code quality acceptable.")

    return {
        "coverage": coverage,
        "coverage_topics_covered": covered,
        "coverage_topics_missing": missing,
        "placement_accuracy": placement_accuracy,
        "code_correctness": code_correctness,
        "downstream_compatibility": downstream_preserved,
        "cited_cells": _cited_cells(response_text),
        "contract_compliance": contract,
        "contract_passed": contract_ok,
        "python_block_count": len(blocks),
        "max_python_block_lines": block_lines,
        "test_passed": passed,
        "evaluation_rationale": " ".join(rationale_parts),
        "response_word_count": len(response_text.split()),
        "response_preview": response_text[:1200],
    }


def _output_stem(test_id: str, kernel_id: int | str) -> str:
    return f"{test_id}_kaggle_kernel_{kernel_id}"


def generate_code_test_summary(payload: dict[str, Any], run: dict[str, Any]) -> str:
    m = run.get("metrics") or {}
    ev = m.get("code_evaluation") or {}
    tn = run.get("test_number") or ""
    name = run.get("test_name") or ""
    kernel_id = run.get("kernel_id") or ""
    passed = bool(ev.get("test_passed"))
    contract = ev.get("contract_compliance") or {}

    lines = [
        f"# Code Test {tn} — {name} (kernel {kernel_id})",
        "",
        "## Notebook",
        "",
        f"- **Test ID:** `{run.get('id', '')}`",
        f"- **Kernel ID:** `{kernel_id}`",
        f"- **URL:** {run.get('url', '')}",
        f"- **Snapshot:** `persistent/{run.get('snapshot_file', '')}`",
        f"- **Mode:** code (chat code + placement, no browser writes)",
        f"- **Live LLM:** {payload.get('live_llm', False)}",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Overall | {'PASS' if passed else 'FAIL'} |",
        f"| Contract compliance | {'PASS' if ev.get('contract_passed') else 'FAIL'} |",
        f"| LLM calls | {m.get('total_llm_calls', 0)} |",
        f"| Tool calls (browser) | {m.get('total_tool_calls', 0)} |",
        f"| Notebook writes | {m.get('notebook_writes', 0)} |",
        f"| Execution time (s) | {m.get('execution_time', 0)} |",
        f"| Placement accuracy | {ev.get('placement_accuracy', 'n/a')} |",
        f"| Code correctness | {ev.get('code_correctness', 'n/a')} |",
        f"| Python blocks / max lines | {ev.get('python_block_count', 0)} / {ev.get('max_python_block_lines', 0)} |",
        f"| Placement guidance | {contract.get('has_placement_guidance', False)} |",
    ]
    if tn == 4:
        lines.extend(
            [
                f"| Acknowledges empty cell | {contract.get('acknowledges_empty_cell', False)} |",
                f"| Defers full code | {contract.get('defers_code_for_empty_cell', False)} |",
            ]
        )

    rationale = str(ev.get("evaluation_rationale") or "")
    if rationale:
        lines.extend(["", "## Evaluation", "", rationale, ""])
    lines.extend(["", f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def generate_code_test_report(payload: dict[str, Any], run: dict[str, Any]) -> str:
    m = run.get("metrics") or {}
    ev = m.get("code_evaluation") or {}
    response = str(run.get("response_text") or ev.get("response_preview") or "")
    lines = [
        f"# Code Test {run.get('test_number', '')} — {run.get('test_name', '')} — Full Report",
        "",
        f"- **Session:** `{run.get('session_id', '')}`",
        f"- **Started:** {payload.get('started_at', '')}",
        "",
        "## Prompt",
        "",
        "```",
        str(run.get("prompt") or "").strip(),
        "```",
        "",
        "## Contract compliance",
        "",
    ]
    for key, val in (ev.get("contract_compliance") or {}).items():
        lines.append(f"- **{key.replace('_', ' ').title()}:** {val}")
    lines.extend(["", "## Model response", "", "```", response[:8000], "```", ""])
    if run.get("error"):
        lines.append(f"## Error\n\n`{run['error']}`\n")
    lines.extend([f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def write_code_test_outputs(payload: dict[str, Any], run: dict[str, Any]) -> dict[str, str]:
    stem = _output_stem(str(run.get("id") or ""), run.get("kernel_id") or "")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    results_path = _LOG_DIR / f"{stem}_results.json"
    summary_path = _LOG_DIR / f"{stem}_summary.md"
    report_path = _LOG_DIR / f"{stem}_report.md"
    results_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary_path.write_text(generate_code_test_summary(payload, run), encoding="utf-8")
    report_path.write_text(generate_code_test_report(payload, run), encoding="utf-8")
    return {"results_path": str(results_path), "summary_path": str(summary_path), "report_path": str(report_path)}


def run_code_test_case(case: dict[str, Any], defaults: dict[str, Any], *, live_llm: bool) -> dict[str, Any]:
    _ensure_live_snapshot(str(case.get("snapshot_file") or ""))
    test_id = str(case.get("id") or "")
    run_result = run_harness_case(
        case, defaults, experiment_id=f"{test_id}_{case.get('kernel_id')}", live_llm=live_llm
    )
    response_text = str(run_result.get("response_text") or "")
    metrics = dict(run_result.get("metrics") or {})
    code_eval = evaluate_code_response(case, response_text, metrics)
    metrics["code_evaluation"] = code_eval
    metrics["completion_status"] = "success" if code_eval.get("test_passed") else "failed"
    metrics["test_passed"] = bool(code_eval.get("test_passed"))
    run_result["metrics"] = metrics
    run_result["test_number"] = case.get("test_number")
    run_result["test_name"] = case.get("test_name")
    return run_result


def generate_suite_index(payload: dict[str, Any]) -> str:
    runs = payload.get("runs") or []
    lines = [
        "# Code Mode Benchmark Suite — Index",
        "",
        f"**Live LLM:** {payload.get('live_llm', False)}",
        f"**Finished:** {payload.get('finished_at', '')}",
        "",
        "| Test | Name | Kernel | Pass | Placement | Code correctness |",
        "|------|------|--------|:----:|----------:|-----------------:|",
    ]
    for run in runs:
        ev = (run.get("metrics") or {}).get("code_evaluation") or {}
        lines.append(
            f"| {run.get('test_number', '')} | {run.get('test_name', '')} | {run.get('kernel_id', '')} | "
            f"{'PASS' if ev.get('test_passed') else 'FAIL'} | {ev.get('placement_accuracy', '—')} | "
            f"{ev.get('code_correctness', '—')} |"
        )
    lines.extend(["", "## Per-test artifacts", ""])
    for run in runs:
        stem = _output_stem(str(run.get("id") or ""), run.get("kernel_id") or "")
        lines.append(f"- `{stem}_summary.md` / `{stem}_report.md`")
    lines.extend(["", f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FYP Code-mode benchmark suite.")
    parser.add_argument("--live-llm", action="store_true")
    parser.add_argument("--test", type=int, choices=[1, 2, 3, 4])
    args = parser.parse_args()

    spec = load_benchmarks(_BENCHMARKS_PATH)
    defaults = dict(spec.get("defaults") or {})
    prompts = list(spec.get("prompts") or [])
    if args.test:
        prompts = [p for p in prompts if int(p.get("test_number") or 0) == args.test]

    started = _utc_now()
    runs = []
    for case in prompts:
        print(f"Running Code Test {case.get('test_number')} — {case.get('test_name')} ...")
        run = run_code_test_case(case, defaults, live_llm=args.live_llm)
        runs.append(run)
        ev = (run.get("metrics") or {}).get("code_evaluation") or {}
        print(
            f"  -> {'PASS' if ev.get('test_passed') else 'FAIL'} | "
            f"placement={ev.get('placement_accuracy')} code={ev.get('code_correctness')}"
        )

    finished = _utc_now()
    payload = {
        "suite": spec.get("suite"),
        "live_llm": args.live_llm,
        "started_at": started,
        "finished_at": finished,
        "runs": runs,
    }
    outputs = []
    for run in runs:
        outputs.append(write_code_test_outputs({**payload, "runs": [run]}, run))

    index_path = _LOG_DIR / "CODE_MODE_BENCHMARK_SUITE_INDEX.md"
    index_path.write_text(generate_suite_index(payload), encoding="utf-8")
    (_LOG_DIR / "CODE_MODE_BENCHMARK_SUITE_results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    passed = sum(1 for r in runs if (r.get("metrics") or {}).get("code_evaluation", {}).get("test_passed"))
    (_LOG_DIR / "CODE_MODE_BENCHMARK_SUITE_summary.md").write_text(
        f"# Code Mode Benchmark Suite\n\nPassed: {passed}/{len(runs)}\nFinished: {finished}\n",
        encoding="utf-8",
    )
    print(f"\nWrote {index_path}")
    for out in outputs:
        print(f"  {out['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
