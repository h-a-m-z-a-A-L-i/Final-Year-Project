#!/usr/bin/env python3
"""Run FYP Ask-mode evaluation tests (1–4) and write per-test summary + report."""

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
_BENCHMARKS_PATH = _SCRIPTS_DIR / "fyp_experiment_benchmarks_ask.json"

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


def _load_snapshot_cells(snapshot_file: str) -> list[dict[str, Any]]:
    path = _NOTEBOOKS_DIR / "persistent" / snapshot_file
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("cells") or [])


def _cell_by_index(cells: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    for cell in cells:
        if int(cell.get("index", -1)) == index:
            return cell
    return None


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


def _count_code_fences(text: str) -> tuple[int, int, bool]:
    """Return (fence_count, max_fence_lines, has_large_block)."""
    fences = re.findall(r"```(?:\w+)?\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if not fences:
        return 0, 0, False
    line_counts = [len(f.splitlines()) for f in fences]
    max_lines = max(line_counts) if line_counts else 0
    return len(fences), max_lines, max_lines >= 12


def _hallucination_signals(
    text: str,
    *,
    cells: list[dict[str, Any]],
    target_cell: int | None,
    ground_truth_terms: list[str],
) -> dict[str, Any]:
    norm = _norm(text)
    max_index = max((int(c.get("index", 0)) for c in cells), default=0)
    cited = {int(m) for m in re.findall(r"\bcell\s+(\d+)\b", norm)}
    invalid_cells = sorted(i for i in cited if i > max_index)

    gt_hit = sum(1 for t in ground_truth_terms if _norm(t) in norm)
    gt_total = len(ground_truth_terms) or 1

    fabricated = []
    if target_cell is not None:
        cell = _cell_by_index(cells, target_cell)
        if cell is not None:
            src = str(cell.get("input") or "")
            if not src.strip():
                if not any(k in norm for k in ("empty", "blank", "no code", "no content")):
                    fabricated.append("did_not_acknowledge_empty_cell")
            else:
                tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", src)
                key_tokens = [t for t in tokens if t.lower() not in ("import", "from", "print", "def", "return")]
                if key_tokens:
                    matched = sum(1 for t in key_tokens[:8] if t.lower() in norm)
                    if matched < 2:
                        fabricated.append("weak_alignment_with_target_cell_source")

    hallucination_rate = round(
        (len(invalid_cells) + len(fabricated) + (1 - gt_hit / gt_total)) / 3,
        3,
    )
    return {
        "cited_cell_indices": sorted(cited),
        "invalid_cell_references": invalid_cells,
        "ground_truth_hits": gt_hit,
        "ground_truth_total": len(ground_truth_terms),
        "fabrication_flags": fabricated,
        "hallucination_rate": min(1.0, max(0.0, hallucination_rate)),
    }


def evaluate_ask_response(
    case: dict[str, Any],
    response_text: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    cells = _load_snapshot_cells(str(case.get("snapshot_file") or ""))
    topics = list(case.get("coverage_topics") or [])
    ground_truth = list(case.get("ground_truth_terms") or [])
    target_cell = case.get("target_cell")
    tn = int(case.get("test_number") or 0)

    tool_calls = int(metrics.get("total_tool_calls") or 0)
    reads = int(metrics.get("notebook_reads") or 0)
    writes = int(metrics.get("notebook_writes") or 0)

    fence_count, max_fence_lines, large_block = _count_code_fences(response_text)
    coverage_hits, covered, missing = _topic_hits(response_text, topics)
    coverage = round(coverage_hits / len(topics), 3) if topics else None

    halluc = _hallucination_signals(
        response_text,
        cells=cells,
        target_cell=int(target_cell) if target_cell is not None else None,
        ground_truth_terms=ground_truth,
    )

    accuracy = None
    if ground_truth:
        accuracy = round(halluc["ground_truth_hits"] / max(1, halluc["ground_truth_total"]), 3)
    elif tn == 1:
        notebook_terms = ["pakistan", "housing", "price", "train", "test", "model", "xgboost", "lightgbm"]
        hits = sum(1 for t in notebook_terms if t in _norm(response_text))
        accuracy = round(hits / len(notebook_terms), 3)

    contract = {
        "no_tool_calls": tool_calls == 0,
        "no_notebook_writes": writes == 0,
        "no_large_code_dump": not large_block,
        "acknowledges_empty_cell": True,
        "asks_clarifying_questions": "?" in response_text,
    }

    if tn == 4:
        norm = _norm(response_text)
        contract["acknowledges_empty_cell"] = any(
            k in norm for k in ("empty", "blank", "no code", "no content", "currently empty")
        )
        contract["asks_clarifying_questions"] = "?" in response_text or any(
            k in norm for k in ("clarif", "which", "what would you like", "could you", "do you want")
        )

    if tn == 3:
        contract["no_large_code_dump"] = not (large_block and fence_count > 0 and max_fence_lines > 8)

    contract_ok = all(
        [
            contract["no_tool_calls"],
            contract["no_notebook_writes"],
            contract["no_large_code_dump"],
        ]
    )
    if tn == 4:
        contract_ok = contract_ok and contract["acknowledges_empty_cell"]

    passed = contract_ok and (coverage is None or coverage >= 0.5)
    if accuracy is not None:
        passed = passed and accuracy >= 0.5

    rationale_parts = []
    if not contract["no_tool_calls"]:
        rationale_parts.append(f"Ask mode dispatched {tool_calls} tool call(s).")
    if not contract["no_notebook_writes"]:
        rationale_parts.append("Notebook write operations detected.")
    if not contract["no_large_code_dump"]:
        rationale_parts.append(f"Response contains large code block ({max_fence_lines} lines).")
    if tn == 4 and not contract["acknowledges_empty_cell"]:
        rationale_parts.append("Did not acknowledge that cell 45 is empty.")
    if coverage is not None and coverage < 0.5:
        rationale_parts.append(f"Low topic coverage ({coverage:.0%}); missing: {', '.join(missing[:5])}.")
    if accuracy is not None and accuracy < 0.5:
        rationale_parts.append(f"Low accuracy vs notebook evidence ({accuracy:.0%}).")
    if not rationale_parts:
        rationale_parts.append("Ask-mode contract satisfied with acceptable coverage/accuracy.")

    return {
        "coverage": coverage,
        "coverage_hits": coverage_hits,
        "coverage_topics_covered": covered,
        "coverage_topics_missing": missing,
        "accuracy": accuracy,
        "hallucination_rate": halluc["hallucination_rate"],
        "hallucination_details": halluc,
        "contract_compliance": contract,
        "contract_passed": contract_ok,
        "code_fence_count": fence_count,
        "max_code_fence_lines": max_fence_lines,
        "test_passed": passed,
        "evaluation_rationale": " ".join(rationale_parts),
        "response_word_count": len(response_text.split()),
        "response_preview": response_text[:1200],
    }


def _output_stem(test_id: str, kernel_id: int | str) -> str:
    return f"{test_id}_kaggle_kernel_{kernel_id}"


def generate_ask_test_summary(payload: dict[str, Any], run: dict[str, Any]) -> str:
    m = run.get("metrics") or {}
    ev = m.get("ask_evaluation") or {}
    tn = run.get("test_number") or ""
    name = run.get("test_name") or run.get("id") or ""
    kernel_id = run.get("kernel_id") or ""
    passed = bool(ev.get("test_passed"))

    lines = [
        f"# Ask Test {tn} — {name} (kernel {kernel_id})",
        "",
        "## Notebook",
        "",
        f"- **Test ID:** `{run.get('id', '')}`",
        f"- **Kernel ID:** `{kernel_id}`",
        f"- **URL:** {run.get('url', '')}",
        f"- **Snapshot:** `persistent/{run.get('snapshot_file', '')}`",
        f"- **Mode:** ask (no tools, no notebook edits)",
        f"- **Live LLM:** {payload.get('live_llm', False)}",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Overall | {'PASS' if passed else 'FAIL'} |",
        f"| Contract compliance | {'PASS' if ev.get('contract_passed') else 'FAIL'} |",
        f"| LLM calls | {m.get('total_llm_calls', 0)} |",
        f"| Tool calls | {m.get('total_tool_calls', 0)} |",
        f"| Execution time (s) | {m.get('execution_time', 0)} |",
    ]

    if ev.get("coverage") is not None:
        lines.append(f"| Coverage | {ev.get('coverage', 0):.1%} ({ev.get('coverage_hits', 0)} topics) |")
    if ev.get("accuracy") is not None:
        lines.append(f"| Accuracy (evidence alignment) | {ev.get('accuracy', 0):.1%} |")
    lines.append(f"| Hallucination rate (heuristic) | {ev.get('hallucination_rate', 0):.1%} |")
    lines.append(f"| Code fences / max lines | {ev.get('code_fence_count', 0)} / {ev.get('max_code_fence_lines', 0)} |")
    lines.append(f"| Response words | {ev.get('response_word_count', 0)} |")

    contract = ev.get("contract_compliance") or {}
    if tn == 4:
        lines.extend(
            [
                f"| Acknowledges empty cell | {contract.get('acknowledges_empty_cell', False)} |",
                f"| Asks clarifying questions | {contract.get('asks_clarifying_questions', False)} |",
                f"| Avoids full code solution | {contract.get('no_large_code_dump', False)} |",
            ]
        )

    rationale = str(ev.get("evaluation_rationale") or "")
    if rationale:
        lines.extend(["", "## Evaluation", "", rationale, ""])

    missing = ev.get("coverage_topics_missing") or []
    if missing:
        lines.extend(["", "## Missing coverage topics", "", ", ".join(missing), ""])

    lines.extend(["", f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def generate_ask_test_report(payload: dict[str, Any], run: dict[str, Any]) -> str:
    m = run.get("metrics") or {}
    ev = m.get("ask_evaluation") or {}
    prompt = str(run.get("prompt") or "").strip()
    response = str(run.get("response_text") or ev.get("response_preview") or "")

    lines = [
        f"# Ask Test {run.get('test_number', '')} — {run.get('test_name', '')} — Full Report",
        "",
        "## Overview",
        "",
        f"- **Test ID:** `{run.get('id', '')}`",
        f"- **Suite:** {payload.get('suite', 'ASK_MODE_BENCHMARK_SUITE')}",
        f"- **Kernel:** `{run.get('kernel_id', '')}`",
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
        "## Ask-mode metrics",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Coverage | {ev.get('coverage', 'n/a')} |",
        f"| Accuracy | {ev.get('accuracy', 'n/a')} |",
        f"| Hallucination rate | {ev.get('hallucination_rate', 'n/a')} |",
        f"| Contract passed | {ev.get('contract_passed', False)} |",
        f"| Tool calls | {m.get('total_tool_calls', 0)} |",
        f"| Notebook reads | {m.get('notebook_reads', 0)} |",
        f"| Notebook writes | {m.get('notebook_writes', 0)} |",
        "",
        "## Contract compliance",
        "",
    ]
    for key, val in (ev.get("contract_compliance") or {}).items():
        lines.append(f"- **{key.replace('_', ' ').title()}:** {val}")

    hall = ev.get("hallucination_details") or {}
    if hall:
        lines.extend(
            [
                "",
                "## Hallucination signals",
                "",
                f"- Invalid cell references: {hall.get('invalid_cell_references', [])}",
                f"- Fabrication flags: {hall.get('fabrication_flags', [])}",
                f"- Ground-truth hits: {hall.get('ground_truth_hits', 0)}/{hall.get('ground_truth_total', 0)}",
                "",
            ]
        )

    lines.extend(
        [
            "## Model response",
            "",
            "```",
            response[:8000],
            "```",
            "",
        ]
    )

    if run.get("error"):
        lines.extend([f"## Error\n\n`{run['error']}`\n"])

    lines.extend([f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def write_ask_test_outputs(payload: dict[str, Any], run: dict[str, Any]) -> dict[str, str]:
    test_id = str(run.get("id") or "ASK_TEST")
    kernel_id = run.get("kernel_id") or ""
    stem = _output_stem(test_id, kernel_id)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    results_path = _LOG_DIR / f"{stem}_results.json"
    summary_path = _LOG_DIR / f"{stem}_summary.md"
    report_path = _LOG_DIR / f"{stem}_report.md"

    results_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary_path.write_text(generate_ask_test_summary(payload, run), encoding="utf-8")
    report_path.write_text(generate_ask_test_report(payload, run), encoding="utf-8")

    return {
        "results_path": str(results_path),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
    }


def run_ask_test_case(case: dict[str, Any], defaults: dict[str, Any], *, live_llm: bool) -> dict[str, Any]:
    snapshot_file = str(case.get("snapshot_file") or "")
    _ensure_live_snapshot(snapshot_file)

    test_id = str(case.get("id") or "")
    kernel_id = case.get("kernel_id")
    experiment_id = f"{test_id}_{kernel_id}"

    run_result = run_harness_case(case, defaults, experiment_id=experiment_id, live_llm=live_llm)
    response_text = str(run_result.get("response_text") or "")

    metrics = dict(run_result.get("metrics") or {})
    ask_eval = evaluate_ask_response(case, response_text, metrics)
    metrics["ask_evaluation"] = ask_eval
    metrics["completion_status"] = "success" if ask_eval.get("test_passed") else "failed"
    metrics["test_passed"] = bool(ask_eval.get("test_passed"))

    run_result["metrics"] = metrics
    run_result["test_number"] = case.get("test_number")
    run_result["test_name"] = case.get("test_name")
    run_result["target_cell"] = case.get("target_cell")
    return run_result


def generate_suite_index(payload: dict[str, Any]) -> str:
    runs = payload.get("runs") or []
    lines = [
        "# Ask Mode Benchmark Suite — Index",
        "",
        f"**Suite:** {payload.get('suite', 'ASK_MODE_BENCHMARK_SUITE')}",
        f"**Live LLM:** {payload.get('live_llm', False)}",
        f"**Finished:** {payload.get('finished_at', '')}",
        "",
        "| Test | Name | Kernel | Pass | Coverage | Accuracy | Hallucination |",
        "|------|------|--------|:----:|---------:|---------:|--------------:|",
    ]
    for run in runs:
        ev = (run.get("metrics") or {}).get("ask_evaluation") or {}
        lines.append(
            f"| {run.get('test_number', '')} | {run.get('test_name', '')} | {run.get('kernel_id', '')} | "
            f"{'PASS' if ev.get('test_passed') else 'FAIL'} | "
            f"{ev.get('coverage', '—')} | {ev.get('accuracy', '—')} | {ev.get('hallucination_rate', '—')} |"
        )
    lines.extend(["", "## Per-test artifacts", ""])
    for run in runs:
        stem = _output_stem(str(run.get("id") or ""), run.get("kernel_id") or "")
        lines.append(f"- `{stem}_summary.md` / `{stem}_report.md`")
    lines.extend(["", f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FYP Ask-mode benchmark suite.")
    parser.add_argument("--live-llm", action="store_true", help="Use real Cerebras LLM (requires API key).")
    parser.add_argument("--regenerate", action="store_true", help="Rebuild markdown from latest JSON results.")
    parser.add_argument("--test", type=int, choices=[1, 2, 3, 4], help="Run a single test by number.")
    args = parser.parse_args()

    spec = load_benchmarks(_BENCHMARKS_PATH)
    defaults = dict(spec.get("defaults") or {})
    prompts = list(spec.get("prompts") or [])
    if args.test:
        prompts = [p for p in prompts if int(p.get("test_number") or 0) == args.test]

    if args.regenerate:
        for case in prompts:
            stem = _output_stem(str(case.get("id") or ""), case.get("kernel_id") or "")
            results_path = _LOG_DIR / f"{stem}_results.json"
            if not results_path.is_file():
                print(f"Skip regenerate — missing {results_path}")
                continue
            payload = json.loads(results_path.read_text(encoding="utf-8"))
            run = next((r for r in payload.get("runs") or [] if r.get("id") == case.get("id")), None)
            if run:
                write_ask_test_outputs(payload, run)
                print(f"Regenerated {stem}")
        index_path = _LOG_DIR / "ASK_MODE_BENCHMARK_SUITE_INDEX.md"
        if index_path.is_file():
            print(f"Index exists: {index_path}")
        return 0

    started = _utc_now()
    runs: list[dict[str, Any]] = []
    outputs: list[dict[str, str]] = []

    for case in prompts:
        print(f"Running Ask Test {case.get('test_number')} — {case.get('test_name')} ...")
        run = run_ask_test_case(case, defaults, live_llm=args.live_llm)
        runs.append(run)
        ev = (run.get("metrics") or {}).get("ask_evaluation") or {}
        print(
            f"  -> {'PASS' if ev.get('test_passed') else 'FAIL'} | "
            f"coverage={ev.get('coverage')} accuracy={ev.get('accuracy')} "
            f"hallucination={ev.get('hallucination_rate')}"
        )

    finished = _utc_now()
    payload: dict[str, Any] = {
        "suite": spec.get("suite") or "ASK_MODE_BENCHMARK_SUITE",
        "agent": spec.get("agent") or "Notebook Agent v1",
        "description": spec.get("description") or "",
        "live_llm": args.live_llm,
        "started_at": started,
        "finished_at": finished,
        "runs": runs,
    }

    for run in runs:
        single = {**payload, "runs": [run]}
        outputs.append(write_ask_test_outputs(single, run))

    index_path = _LOG_DIR / "ASK_MODE_BENCHMARK_SUITE_INDEX.md"
    index_path.write_text(generate_suite_index(payload), encoding="utf-8")

    suite_results = _LOG_DIR / "ASK_MODE_BENCHMARK_SUITE_results.json"
    suite_results.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    suite_summary = _LOG_DIR / "ASK_MODE_BENCHMARK_SUITE_summary.md"
    passed = sum(1 for r in runs if (r.get("metrics") or {}).get("ask_evaluation", {}).get("test_passed"))
    suite_summary.write_text(
        "\n".join(
            [
                "# Ask Mode Benchmark Suite — Summary",
                "",
                f"- **Tests run:** {len(runs)}",
                f"- **Passed:** {passed}/{len(runs)}",
                f"- **Live LLM:** {args.live_llm}",
                f"- **Finished:** {finished}",
                "",
                "See `ASK_MODE_BENCHMARK_SUITE_INDEX.md` for per-test links.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"\nWrote suite index: {index_path}")
    print(f"Wrote suite results: {suite_results}")
    for out in outputs:
        print(f"  {out['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
