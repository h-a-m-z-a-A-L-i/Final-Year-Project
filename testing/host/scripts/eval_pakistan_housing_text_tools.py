#!/usr/bin/env python3
"""
Mock-eval LLM text-format tool batches on Pakistan housing notebook (real JSON context).

Uses <agent_tool_batch> parsing (Cerebras agentic path), mock browser execution,
and scores tool names, indices, ordering, and single-turn batch size.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import testing.host.agentic_mode as agentic_mode  # noqa: E402
from testing.host.agentic_mode import set_dashboard_agentic_enabled  # noqa: E402
from testing.host.agentic_text_tools import (  # noqa: E402
    inject_tool_defaults,
    parse_text_tool_batch,
    strip_tool_batch_from_text,
    text_tool_calling_enabled,
)
from testing.host.config import (  # noqa: E402
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_REACT_MAX_ROUNDS,
    TEMPERATURE,
    TOP_P,
    _LLM_CLIENT,
)
from testing.host.llm_provider import react_min_interval_sec  # noqa: E402
from testing.host.notebook_context import pack_context  # noqa: E402
from testing.host.prompt_engineering import agentic_runtime_enabled, build_chat_messages  # noqa: E402
from testing.host.streaming import _completion_extra_kwargs, _final_text_from_response  # noqa: E402
from testing.host.tool_registry import BROWSER_TOOL_NAMES, registry  # noqa: E402

# Reuse mock + scoring from native eval harness
_NATIVE_EVAL = Path(__file__).parent / "eval_pakistan_housing_llm.py"
_spec = importlib.util.spec_from_file_location("eval_ph_native", _NATIVE_EVAL)
eval_ph = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["eval_ph_native"] = eval_ph
_spec.loader.exec_module(eval_ph)

URL = eval_ph.URL
NOTEBOOK_FILE = eval_ph.NOTEBOOK_FILE
CATBOOST_CELL = eval_ph.CATBOOST_CELL
MockBrowserState = eval_ph.MockBrowserState
ToolCallRecord = eval_ph.ToolCallRecord
CheckResult = eval_ph.CheckResult
ScenarioResult = eval_ph.ScenarioResult
_check = eval_ph._check
_names = eval_ph._names
_read_indices = eval_ph._read_indices
_text_mentions = eval_ph._text_mentions
_execute_tool = eval_ph._execute_tool
_safe = eval_ph._safe

ELASTICNET_VAL_CELL = 70
SVR_CELL = 72
TOTAL_CELLS = 78


def _tool_calls_from_content(content: str, *, url: str) -> list[dict]:
    calls = parse_text_tool_batch(content)
    if not calls:
        return []
    return inject_tool_defaults(calls, url=url, tab_id=None)


def _record_calls(
    round_i: int,
    tool_calls: list[dict],
    browser: MockBrowserState,
    reg,
    prose: str,
) -> list[ToolCallRecord]:
    out: list[ToolCallRecord] = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        fname = fn.get("name") or ""
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        except json.JSONDecodeError:
            parsed = {}
        result = _execute_tool(reg, browser, fname, parsed)
        out.append(
            ToolCallRecord(
                round=round_i + 1,
                name=fname,
                args=parsed,
                result_ok=bool(result.get("ok")),
                reasoning=(prose or "")[:200],
            )
        )
    return out


def run_text_tool_scenario(
    *,
    prompt: str,
    max_rounds: int,
    min_interval: float,
    expect_single_batch: bool = False,
) -> tuple[list[ToolCallRecord], list[str], str, int, str | None]:
    if _LLM_CLIENT is None:
        return [], [], "", 0, "No LLM client configured (set CEREBRAS_API_KEY in .env)"
    if not text_tool_calling_enabled(LLM_PROVIDER, agentic=True):
        return [], [], "", 0, "AGENTIC_TEXT_TOOLS disabled for this provider"

    agentic_mode.LLM_AGENTIC_ENABLED = True
    set_dashboard_agentic_enabled(True)
    if not agentic_runtime_enabled("agentic"):
        return [], [], "", 0, "Agentic mode gate failed"

    pack = pack_context(mode="agentic", url=URL, prompt=prompt)
    messages = build_chat_messages(
        mode="agentic",
        user_prompt=prompt,
        history=[],
        context=pack.text,
        notebook_url=URL,
        include_tools=True,
        text_tool_calls=True,
        turn_tail=(
            "Respond with <agent_tool_batch>[...]</agent_tool_batch> containing every required tool "
            "in one JSON array when the task needs tools."
        ),
    )
    extra = _completion_extra_kwargs()
    reg = registry()
    raw = json.loads(NOTEBOOK_FILE.read_text(encoding="utf-8"))
    browser = MockBrowserState(raw.get("cells") or [])

    tool_messages = list(messages)
    records: list[ToolCallRecord] = []
    reasoning: list[str] = []
    final_text = ""
    first_batch_size = 0

    for round_i in range(max_rounds):
        if round_i > 0 and min_interval > 0:
            time.sleep(min_interval)

        try:
            resp = _LLM_CLIENT.chat.completions.create(
                messages=tool_messages,
                model=LLM_MODEL,
                temperature=min(TEMPERATURE, 0.2),
                top_p=TOP_P,
                **extra,
            )
        except Exception as e:
            return records, reasoning, final_text, first_batch_size, f"LLM API error round {round_i + 1}: {e}"

        content = _final_text_from_response(resp)
        prose = strip_tool_batch_from_text(content).strip()
        if prose:
            reasoning.append(prose)

        tool_calls = _tool_calls_from_content(content, url=URL)
        if round_i == 0 and tool_calls:
            first_batch_size = len(tool_calls)

        if not tool_calls:
            final_text = content.strip() or prose or final_text
            break

        tool_messages.append({
            "role": "assistant",
            "content": prose,
            "tool_calls": tool_calls,
        })

        batch_records: list[ToolCallRecord] = []
        for tc_idx, tc in enumerate(tool_calls):
            fn = tc.get("function") or {}
            fname = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except json.JSONDecodeError:
                parsed = {}
            result = _execute_tool(reg, browser, fname, parsed)
            batch_records.append(
                ToolCallRecord(
                    round=round_i + 1,
                    name=fname,
                    args=parsed,
                    result_ok=bool(result.get("ok")),
                    reasoning=(prose or "")[:200],
                )
            )
            tool_call_id = tc.get("id") or f"text_{round_i}_{tc_idx}"
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(result, ensure_ascii=False)[:8000],
            })
        records.extend(batch_records)

        if expect_single_batch and round_i == 0 and tool_calls:
            tool_messages.append({
                "role": "user",
                "content": "Tools executed. Reply with a brief markdown summary only (no <agent_tool_batch>).",
            })
            if min_interval > 0:
                time.sleep(min_interval)
            try:
                final_resp = _LLM_CLIENT.chat.completions.create(
                    messages=tool_messages,
                    model=LLM_MODEL,
                    temperature=min(TEMPERATURE, 0.2),
                    top_p=TOP_P,
                    **extra,
                )
                final_text = _final_text_from_response(final_resp).strip()
            except Exception as e:
                return records, reasoning, final_text, first_batch_size, f"Summary call failed: {e}"
            break

    if not final_text and records and not expect_single_batch:
        if min_interval > 0:
            time.sleep(min_interval)
        try:
            final_resp = _LLM_CLIENT.chat.completions.create(
                messages=tool_messages,
                model=LLM_MODEL,
                temperature=min(TEMPERATURE, 0.2),
                top_p=TOP_P,
                **extra,
            )
            final_text = _final_text_from_response(final_resp).strip()
        except Exception as e:
            return records, reasoning, final_text, first_batch_size, f"Final synthesis failed: {e}"

    return records, reasoning, final_text, first_batch_size, None


# --- Text-batch scorers ---


def _run_cells(calls: list[ToolCallRecord]) -> list[int]:
    return [
        int(c.args["cell_index"])
        for c in calls
        if c.name == "run_cell" and c.args.get("cell_index") is not None
    ]


def _order_ok(names: list[str], *sequence: str) -> bool:
    pos = 0
    for want in sequence:
        try:
            pos = names.index(want, pos) + 1
        except ValueError:
            return False
    return True


def score_batch_run_three(calls: list[ToolCallRecord], final: str, *, first_batch: int) -> list[CheckResult]:
    names = _names(calls)
    runs = _run_cells(calls)
    want = {CATBOOST_CELL, ELASTICNET_VAL_CELL, SVR_CELL}
    return [
        _check("single_turn_batch", first_batch >= 3, f"first batch size={first_batch}"),
        _check("three_run_cell", names.count("run_cell") >= 3, f"runs={runs}"),
        _check("correct_indices", want.issubset(set(runs)), f"expected {sorted(want)} got {sorted(set(runs))}"),
        _check("all_mock_ok", all(c.result_ok for c in calls), "mock execution"),
        _check("all_have_url", all(c.args.get("url") == URL for c in calls if c.args), "url arg"),
    ]


def score_batch_insert_importance(calls: list[ToolCallRecord], final: str, *, first_batch: int) -> list[CheckResult]:
    names = _names(calls)
    insert_near = any(
        c.name == "insert_cell"
        and abs(int(c.args.get("index", 0)) - CATBOOST_CELL) <= 1
        for c in calls
        if c.args.get("index") is not None
    )
    edit_ok = any(
        c.name == "edit_cell_by_index"
        and _text_mentions(str(c.args.get("content") or ""), "feature_importance", "get_feature_importance")
        for c in calls
    )
    return [
        _check("single_turn_batch", first_batch >= 3, f"first batch size={first_batch}"),
        _check("has_insert", "insert_cell" in names, str(names)),
        _check("insert_near_catboost", insert_near, f"anchor ~{CATBOOST_CELL}"),
        _check("has_edit_importance", edit_ok, "edit content"),
        _check("has_run", "run_cell" in names, str(names)),
        _check(
            "order_insert_edit_run",
            _order_ok(names, "insert_cell", "edit_cell_by_index", "run_cell"),
            f"order={names}",
        ),
    ]


def score_batch_markdown_plus_code(calls: list[ToolCallRecord], final: str, *, first_batch: int) -> list[CheckResult]:
    names = _names(calls)
    md = any(
        c.name == "creating_markdown_by_index" and int(c.args.get("index", 0)) == CATBOOST_CELL
        for c in calls
        if c.args.get("index") is not None
    )
    edit_md = any(
        c.name == "edit_cell_by_index"
        and _text_mentions(str(c.args.get("content") or ""), "gpu", "early stopping", "early_stopping")
        for c in calls
    ) or _text_mentions(final, "gpu", "early_stopping", "early stopping")
    edit_code = any(
        c.name == "edit_cell_by_index"
        and _text_mentions(str(c.args.get("content") or ""), "feature_importance", "get_feature_importance")
        for c in calls
    )
    return [
        _check("single_turn_batch", first_batch >= 4, f"first batch size={first_batch}"),
        _check("markdown_above_catboost", md, str(names)),
        _check("markdown_content", edit_md, "GPU / early stopping in markdown"),
        _check("code_importance", edit_code, "importance code cell"),
        _check("has_run", "run_cell" in names, str(names)),
        _check("tool_count", len(calls) >= 4, f"total={len(calls)}"),
    ]


def score_read_discover(calls: list[ToolCallRecord], final: str, *, first_batch: int) -> list[CheckResult]:
    return eval_ph.score_discovery_catboost(calls, final) + [
        _check("used_text_batch", first_batch >= 1 or bool(calls), f"first batch={first_batch}"),
    ]


def score_read_correlation(calls: list[ToolCallRecord], final: str, *, first_batch: int) -> list[CheckResult]:
    return eval_ph.score_correlation_analysis(calls, final) + [
        _check("used_text_batch", first_batch >= 1 or bool(calls), f"first batch={first_batch}"),
    ]


def score_notebook_stats(calls: list[ToolCallRecord], final: str, *, first_batch: int) -> list[CheckResult]:
    checks = eval_ph.score_notebook_stats(calls, final)
    checks.append(_check("mentions_78_cells", _text_mentions(final, "78") or str(TOTAL_CELLS) in (final or ""), final[:80]))
    return checks


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "batch_run_three_models",
        "title": "Single batch: run CatBoost + ElasticNet val + SVR cells",
        "prompt": (
            "Run these code cells so outputs refresh: cell 50 (CatBoost GPU training), "
            "cell 70 (Elastic Net validation RMSE), and cell 72 (SVR RandomizedSearchCV). "
            "Emit ALL run_cell tools in ONE <agent_tool_batch> — no prose, no one-tool-per-message."
        ),
        "score_fn": score_batch_run_three,
        "expect_single_batch": True,
    },
    {
        "id": "batch_insert_importance",
        "title": "Single batch: insert + edit feature importance + run below CatBoost",
        "prompt": (
            "Insert a new code cell below the CatBoost training cell (cell 50), edit it to print "
            "the top 10 feature importances from catboost_model using feature_cols, then run that cell. "
            "Put insert_cell, edit_cell_by_index, and run_cell in ONE <agent_tool_batch>."
        ),
        "score_fn": score_batch_insert_importance,
        "expect_single_batch": True,
    },
    {
        "id": "batch_markdown_and_code",
        "title": "Single batch: markdown above CatBoost + code cell below + run",
        "prompt": (
            "In one <agent_tool_batch>: (1) add markdown above cell 50 noting task_type GPU and "
            "early_stopping_rounds=50, (2) insert a code cell below 50, (3) edit it to print "
            "catboost_model.get_feature_importance() top 10, (4) run the new code cell. "
            "All tools in one JSON array."
        ),
        "score_fn": score_batch_markdown_plus_code,
        "expect_single_batch": True,
    },
    {
        "id": "read_discover_catboost",
        "title": "Read: locate CatBoost training cell (text tools)",
        "prompt": (
            "Use notebook read tools to find where catboost_model is trained. "
            "Report the 1-based cell index. Use <agent_tool_batch> for tool calls."
        ),
        "score_fn": score_read_discover,
        "expect_single_batch": False,
    },
    {
        "id": "read_correlation",
        "title": "Read: corr_with_price analysis (text tools)",
        "prompt": (
            "Find where corr_with_price is computed. Explain why Pearson correlation is weak for "
            "categorical features. Read tools only — use <agent_tool_batch>."
        ),
        "score_fn": score_read_correlation,
        "expect_single_batch": False,
    },
    {
        "id": "notebook_stats",
        "title": "Read: total cell count (text tools)",
        "prompt": (
            "How many total cells are in this notebook? Use notebook_list_cells or "
            "notebook_snapshot_status in <agent_tool_batch>. Read only."
        ),
        "score_fn": score_notebook_stats,
        "expect_single_batch": False,
    },
]


def run_scenario(spec: dict, max_rounds: int, min_interval: float) -> ScenarioResult:
    t0 = time.perf_counter()
    calls, reasoning, final, first_batch, err = run_text_tool_scenario(
        prompt=spec["prompt"],
        max_rounds=max_rounds,
        min_interval=min_interval,
        expect_single_batch=bool(spec.get("expect_single_batch")),
    )
    checks = spec["score_fn"](calls, final, first_batch=first_batch) if calls or final else []
    if err:
        checks = checks or []
        checks.append(_check("llm_run", False, err))
    passed = sum(1 for c in checks if c.passed)
    max_pts = len(checks)
    return ScenarioResult(
        scenario_id=spec["id"],
        title=spec["title"],
        prompt=spec["prompt"],
        tool_calls=calls,
        reasoning_snippets=reasoning + ([f"first_batch_size={first_batch}"] if first_batch else []),
        final_text=final,
        checks=checks,
        score=float(passed),
        max_score=float(max_pts),
        error=err,
        elapsed_sec=time.perf_counter() - t0,
    )


def print_report(results: list[ScenarioResult]) -> None:
    print("=" * 76)
    print("TEXT-TOOL BATCH EVAL — Pakistan Housing Notebook (mock execution)")
    print("=" * 76)
    print(f"Provider : {LLM_PROVIDER}")
    print(f"Model    : {LLM_MODEL}")
    print(f"Notebook : {NOTEBOOK_FILE.name}")
    print(f"Cells    : {TOTAL_CELLS} (CatBoost={CATBOOST_CELL})")
    print(f"Scenarios: {len(results)}")
    print()

    total_score = 0.0
    total_max = 0.0
    for r in results:
        pct = 100.0 * r.score / r.max_score if r.max_score else 0.0
        total_score += r.score
        total_max += r.max_score
        status = "PASS" if r.score == r.max_score else ("PARTIAL" if r.score > 0 else "FAIL")
        print(f"## [{status}] {r.scenario_id} — {r.title}")
        print(f"   Score: {r.score:.0f}/{r.max_score:.0f} ({pct:.0f}%)  Time: {r.elapsed_sec:.1f}s")
        if r.error:
            print(f"   ERROR: {r.error}")
        print(f"   Tool sequence ({len(r.tool_calls)} calls):")
        for tc in r.tool_calls:
            args_brief = {
                k: v
                for k, v in tc.args.items()
                if k in ("url", "cell_index", "index", "query", "symbol", "direction")
            }
            ok = "ok" if tc.result_ok else "FAIL"
            print(f"     r{tc.round} [{ok}] {tc.name} {args_brief}")
        print("   Checks:")
        for c in r.checks:
            mark = "PASS" if c.passed else "FAIL"
            print(f"     [{mark}] {c.name}: {c.detail}")
        if r.final_text:
            print(f"   Final: {_safe(r.final_text, 220)}...")
        print()

    overall = 100.0 * total_score / total_max if total_max else 0.0
    print("=" * 76)
    print(f"OVERALL SCORE: {total_score:.0f}/{total_max:.0f} ({overall:.1f}%)")
    print("=" * 76)


def main() -> int:
    parser = argparse.ArgumentParser(description="Text-tool batch eval on Pakistan housing notebook")
    parser.add_argument("--scenario", action="append", help="Run only these scenario ids")
    parser.add_argument("--max-rounds", type=int, default=min(LLM_REACT_MAX_ROUNDS, 8))
    parser.add_argument(
        "--json-out",
        default=str(REPO / "testing/host/data/meta/eval_pakistan_housing_text_tools.json"),
    )
    parser.add_argument("--min-interval", type=float, default=None)
    parser.add_argument(
        "--complex-only",
        action="store_true",
        help="Run only single-turn batch write scenarios",
    )
    args = parser.parse_args()

    if not NOTEBOOK_FILE.is_file():
        print(f"Missing notebook JSON: {NOTEBOOK_FILE}", file=sys.stderr)
        return 1
    if _LLM_CLIENT is None:
        print("ERROR: Set CEREBRAS_API_KEY in .env", file=sys.stderr)
        return 1
    if not text_tool_calling_enabled(LLM_PROVIDER, agentic=True):
        print("ERROR: AGENTIC_TEXT_TOOLS not enabled for provider", file=sys.stderr)
        return 1

    min_interval = args.min_interval
    if min_interval is None:
        min_interval = react_min_interval_sec(LLM_PROVIDER)

    specs = SCENARIOS
    if args.complex_only:
        specs = [s for s in SCENARIOS if s.get("expect_single_batch")]
    if args.scenario:
        wanted = set(args.scenario)
        specs = [s for s in specs if s["id"] in wanted]
        if not specs:
            print(f"No matching scenarios. Available: {[s['id'] for s in SCENARIOS]}", file=sys.stderr)
            return 1

    print(f"Running {len(specs)} scenario(s) | text_tools=True | interval={min_interval:.1f}s")
    results = [run_scenario(s, args.max_rounds, min_interval) for s in specs]
    print_report(results)

    out_path = Path(args.json_out)
    payload = {
        "mode": "text_tool_batch",
        "url": URL,
        "notebook_file": str(NOTEBOOK_FILE),
        "provider": LLM_PROVIDER,
        "model": LLM_MODEL,
        "results": [
            {
                **{k: v for k, v in asdict(r).items() if k != "tool_calls"},
                "tool_calls": [asdict(t) for t in r.tool_calls],
            }
            for r in results
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON report: {out_path}")

    all_pass = all(r.score == r.max_score and r.max_score > 0 for r in results)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
