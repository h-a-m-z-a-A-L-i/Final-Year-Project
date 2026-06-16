#!/usr/bin/env python3
"""Run BENCHMARK_ML_PIPELINE on each configured notebook snapshot separately."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = Path(__file__).resolve().parent
_HOST_DIR = _SCRIPTS_DIR.parent
_LOG_DIR = _HOST_DIR / "data" / "logs"
_NOTEBOOKS_DIR = _HOST_DIR / "data" / "notebooks"
_BENCHMARKS_PATH = _SCRIPTS_DIR / "fyp_experiment_benchmarks_ml_pipeline.json"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from testing.host.scripts.fyp_experiment_runner import (  # noqa: E402
    load_benchmarks,
    regenerate_ml_pipeline_docs_from_results,
    run_experiment,
    write_ml_pipeline_outputs,
)


def _ensure_live_snapshot(snapshot_file: str) -> None:
    """Copy persistent snapshot to live/ if missing (experiment setup only)."""
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


def _results_path_for_kernel(kernel_id: int | str) -> Path:
    return _LOG_DIR / f"BENCHMARK_ML_PIPELINE_kaggle_kernel_{kernel_id}_results.json"


def run_ml_pipeline_benchmarks(*, live_llm: bool, only: set[str] | None) -> list[dict]:
    bench = load_benchmarks(_BENCHMARKS_PATH)
    outputs: list[dict] = []

    for case in bench.get("prompts") or []:
        prompt_id = str(case.get("id") or "")
        if only and prompt_id not in only:
            continue

        kernel_id = case.get("kernel_id")
        snapshot_file = str(case.get("snapshot_file") or "")
        _ensure_live_snapshot(snapshot_file)

        experiment_id = f"BENCHMARK_ML_PIPELINE_{kernel_id}"
        payload = run_experiment(
            benchmarks_path=_BENCHMARKS_PATH,
            mode="harness",
            live_llm=live_llm,
            only={prompt_id},
            experiment_id=experiment_id,
        )
        payload["benchmark"] = "BENCHMARK_ML_PIPELINE"

        run = (payload.get("runs") or [{}])[0]
        paths = write_ml_pipeline_outputs(payload, run, kernel_id=kernel_id)

        outputs.append(
            {
                "kernel_id": kernel_id,
                "snapshot_file": snapshot_file,
                "completion_status": (run.get("metrics") or {}).get("completion_status"),
                **paths,
            }
        )
        print(f"Kernel {kernel_id}: {paths['results_path']}")
        print(f"Kernel {kernel_id}: {paths['summary_path']}")
        print(f"Kernel {kernel_id}: {paths['report_path']}")

    return outputs


def regenerate_all_summaries() -> list[dict]:
    """Regenerate summary + report from existing per-kernel results JSON."""
    outputs: list[dict] = []
    for results_path in sorted(_LOG_DIR.glob("BENCHMARK_ML_PIPELINE_kaggle_kernel_*_results.json")):
        paths = regenerate_ml_pipeline_docs_from_results(results_path)
        outputs.append({"results_path": str(results_path), **paths})
        print(f"Regenerated: {paths['summary_path']}")
        print(f"Regenerated: {paths['report_path']}")
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run BENCHMARK_ML_PIPELINE per notebook kernel")
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Use real Cerebras API (requires CEREBRAS_API_KEY)",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="ID",
        help="Run only benchmark ids (e.g. BENCHMARK_ML_PIPELINE_113620421)",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Rebuild summary + report from existing results JSON (no LLM run)",
    )
    args = parser.parse_args(argv)

    if args.regenerate:
        outputs = regenerate_all_summaries()
    else:
        only = set(args.only) if args.only else None
        outputs = run_ml_pipeline_benchmarks(live_llm=bool(args.live_llm), only=only)

    print(json.dumps(outputs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
