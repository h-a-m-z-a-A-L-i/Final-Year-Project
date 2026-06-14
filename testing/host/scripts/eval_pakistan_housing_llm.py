#!/usr/bin/env python3
"""
Evaluate LLM agentic tool-calling on the Pakistan housing notebook JSON.

Runs multiple complex scenarios (read-only analysis + write workflows), executes
real local read tools, mocks browser write tools statefully, and scores decisions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import testing.host.agentic_mode as agentic_mode  # noqa: E402
from testing.host.agentic_mode import set_dashboard_agentic_enabled  # noqa: E402
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
from testing.host.prompt_engineering import (  # noqa: E402
    agentic_runtime_enabled,
    build_chat_messages,
)
from testing.host.streaming import (  # noqa: E402
    _completion_extra_kwargs,
    _final_text_from_response,
    _parallel_tool_calls_flag,
)
from testing.host.tool_registry import BROWSER_TOOL_NAMES, registry  # noqa: E402

URL = "https://www.kaggle.com/code/codekey/pakistan-housing/edit"
NOTEBOOK_FILE = (
    REPO
    / "testing/host/data/notebooks/https___www_kaggle_com_code_codekey_pakistan_housing_edit.json"
)

# Ground truth from scraped JSON
CATBOOST_CELL = 50
CORR_CELL = 8
CORR_PRINT_CELL = 10


@dataclass
class ToolCallRecord:
    round: int
    name: str
    args: dict
    result_ok: bool
    reasoning: str = ""


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class ScenarioResult:
    scenario_id: str
    title: str
    prompt: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    reasoning_snippets: list[str] = field(default_factory=list)
    final_text: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    score: float = 0.0
    max_score: float = 0.0
    error: str | None = None
    elapsed_sec: float = 0.0


class MockBrowserState:
    def __init__(self, cells: list[dict]):
        self.cells = [dict(c) for c in cells]

    def _reindex(self) -> None:
        for i, c in enumerate(self.cells, start=1):
            c["index"] = i

    def select_cell(self, args: dict) -> dict:
        idx = int(args["cell_index"])
        if not any(int(c.get("index", 0)) == idx for c in self.cells):
            return {"ok": False, "error": f"Cell {idx} not found", "tool": "select_cell_by_index"}
        return {"ok": True, "tool": "select_cell_by_index", "cell_index": idx, "phase": "selected"}

    def click_cell(self, args: dict) -> dict:
        idx = int(args.get("cell_index") or 1)
        return {"ok": True, "tool": "click_cell", "cell_index": idx, "domIndex": idx - 1, "phase": "focused"}

    def insert_cell(self, args: dict) -> dict:
        anchor = int(args["index"])
        direction = args.get("direction", "below")
        insert_at = anchor if direction == "below" else max(0, anchor - 1)
        new_cell = {"type": "code", "index": 0, "input": "", "output": ""}
        insert_at = min(insert_at, len(self.cells))
        self.cells.insert(insert_at, new_cell)
        self._reindex()
        return {
            "ok": True,
            "tool": "insert_cell",
            "new_cell_index": insert_at + 1,
            "new_dom_index": insert_at,
            "direction": direction,
            "phase": "inserted",
        }

    def edit_cell(self, args: dict) -> dict:
        idx = int(args["cell_index"])
        content = args.get("content") or ""
        for c in self.cells:
            if int(c.get("index", 0)) == idx:
                c["input"] = content
                return {
                    "ok": True,
                    "tool": "edit_cell_by_index",
                    "cell_index": idx,
                    "phase": "content_set",
                    "chars": len(content),
                }
        return {"ok": False, "error": f"Cell {idx} not found", "tool": "edit_cell_by_index"}

    def run_cell(self, args: dict) -> dict:
        idx = int(args["cell_index"])
        return {"ok": True, "tool": "run_cell", "cell_index": idx, "phase": "executed"}

    def delete_cell(self, args: dict) -> dict:
        idx = int(args["cell_index"])
        before = len(self.cells)
        self.cells = [c for c in self.cells if int(c.get("index", 0)) != idx]
        if len(self.cells) == before:
            return {"ok": False, "error": f"Cell {idx} not found", "tool": "delete_by_index"}
        self._reindex()
        return {"ok": True, "tool": "delete_by_index", "cell_index": idx, "phase": "deleted"}

    def create_markdown(self, args: dict) -> dict:
        anchor = int(args["index"])
        insert_at = max(0, anchor - 1)
        self.cells.insert(insert_at, {"type": "markdown", "index": 0, "input": "", "output": ""})
        self._reindex()
        return {
            "ok": True,
            "tool": "creating_markdown_by_index",
            "new_cell_index": insert_at + 1,
            "phase": "markdown_inserted",
        }

    def dispatch(self, name: str, args: dict) -> dict:
        handlers = {
            "select_cell_by_index": self.select_cell,
            "click_cell": self.click_cell,
            "insert_cell": self.insert_cell,
            "edit_cell_by_index": self.edit_cell,
            "run_cell": self.run_cell,
            "delete_by_index": self.delete_cell,
            "creating_markdown_by_index": self.create_markdown,
        }
        fn = handlers.get(name)
        if not fn:
            return {"ok": False, "error": f"unknown browser tool {name}"}
        return fn(args)


def _execute_tool(
    reg,
    browser: MockBrowserState,
    fname: str,
    args: dict,
) -> dict:
    args = dict(args or {})
    args.setdefault("url", URL)
    if fname in BROWSER_TOOL_NAMES:
        return browser.dispatch(fname, args)
    try:
        return reg.call(fname, args)
    except Exception as e:
        return {"ok": False, "error": str(e), "tool": fname}


def _parse_tool_calls(assistant_msg: dict) -> list[dict]:
    return assistant_msg.get("tool_calls") or []


def run_agentic_scenario(
    *,
    prompt: str,
    max_rounds: int,
    min_interval: float,
) -> tuple[list[ToolCallRecord], list[str], str, str | None]:
    if _LLM_CLIENT is None:
        return [], [], "", "No LLM client configured (set CEREBRAS_API_KEY or GEMINI_API_KEY in .env)"

    # Eval harness bypasses dashboard/server gates so we can test tool decisions.
    agentic_mode.LLM_AGENTIC_ENABLED = True
    set_dashboard_agentic_enabled(True)
    if not agentic_runtime_enabled("agentic"):
        return [], [], "", "Agentic mode gate failed"

    pack = pack_context(mode="agentic", url=URL, prompt=prompt)
    messages = build_chat_messages(
        mode="agentic",
        user_prompt=prompt,
        history=[],
        context=pack.text,
        notebook_url=URL,
        include_tools=True,
    )
    from testing.host.tool_registry import build_cerebras_tools

    tools = build_cerebras_tools(include_browser=True)
    parallel = _parallel_tool_calls_flag()
    extra = _completion_extra_kwargs()
    reg = registry()
    raw = json.loads(NOTEBOOK_FILE.read_text(encoding="utf-8"))
    browser = MockBrowserState(raw.get("cells") or [])

    tool_messages = list(messages)
    records: list[ToolCallRecord] = []
    reasoning: list[str] = []
    final_text = ""

    for round_i in range(max_rounds):
        if round_i > 0 and min_interval > 0:
            time.sleep(min_interval)

        try:
            resp = _LLM_CLIENT.chat.completions.create(
                messages=tool_messages,
                model=LLM_MODEL,
                tools=tools,
                parallel_tool_calls=parallel,
                temperature=min(TEMPERATURE, 0.3),
                top_p=TOP_P,
                **extra,
            )
        except Exception as e:
            return records, reasoning, final_text, f"LLM API error round {round_i + 1}: {e}"

        dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
        choice = (dumped.get("choices") or [{}])[0]
        assistant_msg = choice.get("message") or {}
        prose = (assistant_msg.get("content") or "").strip()
        if prose:
            reasoning.append(prose)

        tool_calls = _parse_tool_calls(assistant_msg)
        if not tool_calls:
            final_text = _final_text_from_response(resp).strip() or prose or final_text
            break

        tool_messages.append({
            "role": "assistant",
            "content": assistant_msg.get("content") or "",
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc.get("function") or {}
            fname = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except json.JSONDecodeError:
                parsed = {}

            result = _execute_tool(reg, browser, fname, parsed)
            records.append(
                ToolCallRecord(
                    round=round_i + 1,
                    name=fname,
                    args=parsed,
                    result_ok=bool(result.get("ok")),
                    reasoning=prose[:200],
                )
            )
            tool_call_id = tc.get("id") or f"call_{fname}_{round_i}"
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    if not final_text and tool_messages:
        if min_interval > 0:
            time.sleep(min_interval)
        try:
            final_resp = _LLM_CLIENT.chat.completions.create(
                messages=tool_messages,
                model=LLM_MODEL,
                temperature=min(TEMPERATURE, 0.3),
                top_p=TOP_P,
                **extra,
            )
            final_text = _final_text_from_response(final_resp).strip()
        except Exception as e:
            return records, reasoning, final_text, f"Final synthesis failed: {e}"

    return records, reasoning, final_text, None


# --- Scoring helpers ---

def _names(calls: list[ToolCallRecord]) -> list[str]:
    return [c.name for c in calls]


def _read_indices(calls: list[ToolCallRecord]) -> list[int]:
    out = []
    for c in calls:
        for key in ("cell_index", "index"):
            v = c.args.get(key)
            if v is not None:
                try:
                    out.append(int(v))
                except (TypeError, ValueError):
                    pass
    return out


def _first_index(names: list[str], prefix: str) -> int | None:
    for i, n in enumerate(names):
        if n.startswith(prefix) or n in BROWSER_TOOL_NAMES:
            if n.startswith(prefix):
                return i
            if n in BROWSER_TOOL_NAMES:
                return i
    return None


def _text_mentions(text: str, *patterns: str) -> bool:
    t = (text or "").lower()
    return any(p.lower() in t for p in patterns)


def _safe(s: str, limit: int = 0) -> str:
    text = (s or "").encode("ascii", errors="replace").decode("ascii")
    return text[:limit] if limit else text


def _check(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=passed, detail=detail)


def score_discovery_catboost(calls: list[ToolCallRecord], final: str) -> list[CheckResult]:
    names = _names(calls)
    checks = [
        _check(
            "used_read_tool",
            any(n.startswith("notebook_") for n in names),
            f"tools={names[:8]}",
        ),
        _check(
            "used_search_or_symbol",
            any(n in ("notebook_search", "notebook_find_symbol", "notebook_get_cell") for n in names),
            f"search/symbol/get in {names}",
        ),
        _check(
            "no_premature_write",
            not any(n in BROWSER_TOOL_NAMES for n in names[:2]),
            "first two calls should be read-only",
        ),
        _check(
            "found_catboost_cell",
            CATBOOST_CELL in _read_indices(calls)
            or _text_mentions(final, "50", "cell 50", f"cell [{CATBOOST_CELL}]"),
            f"indices={_read_indices(calls)} final mentions 50={_text_mentions(final, '50')}",
        ),
        _check(
            "mentions_catboost",
            _text_mentions(final, "catboost", "catboostregressor"),
            "final answer should reference CatBoost",
        ),
        _check(
            "all_calls_have_url",
            all(c.args.get("url") == URL for c in calls if c.args),
            "every tool call must pass session url",
        ),
    ]
    return checks


def score_correlation_analysis(calls: list[ToolCallRecord], final: str) -> list[CheckResult]:
    names = _names(calls)
    target_cells = {CORR_CELL, CORR_PRINT_CELL}
    hit_cell = bool(target_cells & set(_read_indices(calls))) or any(
        n in ("notebook_search", "notebook_get_cell") for n in names
    )
    checks = [
        _check("used_read_tool", any(n.startswith("notebook_") for n in names), str(names)),
        _check(
            "searched_or_read_corr",
            hit_cell or _text_mentions(final, "corr_with_price", "correlation"),
            "should inspect correlation cells",
        ),
        _check(
            "explains_categorical_limitation",
            _text_mentions(
                final,
                "categorical",
                "factoriz",
                "linear",
                "one-hot",
                "non-linear",
                "pearson",
            ),
            "should discuss why Pearson is weak here",
        ),
        _check(
            "no_write_tools",
            not any(n in BROWSER_TOOL_NAMES for n in names),
            f"read-only task; writes={ [n for n in names if n in BROWSER_TOOL_NAMES] }",
        ),
    ]
    return checks


def score_dependency_order(calls: list[ToolCallRecord], final: str) -> list[CheckResult]:
    names = _names(calls)
    graph_tools = {"notebook_cell_neighbors", "notebook_graph_query", "notebook_find_symbol"}
    checks = [
        _check(
            "used_graph_or_neighbors",
            any(n in graph_tools for n in names),
            f"expected graph/neighbors; got {names}",
        ),
        _check(
            "referenced_catboost",
            _text_mentions(final, "catboost", "50") or CATBOOST_CELL in _read_indices(calls),
            "should anchor on catboost cell",
        ),
        _check(
            "mentions_upstream_or_order",
            _text_mentions(final, "upstream", "before", "depend", "order", "run", "execut"),
            "should discuss execution order",
        ),
        _check("no_write_tools", not any(n in BROWSER_TOOL_NAMES for n in names), str(names)),
    ]
    return checks


def score_insert_importance(calls: list[ToolCallRecord], final: str) -> list[CheckResult]:
    names = _names(calls)
    read_first = True
    if names:
        first_write = next((i for i, n in enumerate(names) if n in BROWSER_TOOL_NAMES), len(names))
        first_read = next((i for i, n in enumerate(names) if n.startswith("notebook_")), len(names))
        read_first = first_read < first_write
    insert_ok = "insert_cell" in names
    edit_ok = "edit_cell_by_index" in names
    content_ok = any(
        c.name == "edit_cell_by_index"
        and _text_mentions(str(c.args.get("content") or ""), "feature_importance", "get_feature_importance")
        for c in calls
    )
    anchor_ok = any(
        c.name == "insert_cell" and abs(int(c.args.get("index", 0)) - CATBOOST_CELL) <= 2
        for c in calls
        if c.args.get("index") is not None
    )
    checks = [
        _check("read_before_write", read_first, f"order={names}"),
        _check("called_insert_cell", insert_ok, str(names)),
        _check("called_edit_after_insert", edit_ok, str(names)),
        _check("edit_has_importance_code", content_ok, "edit content should use get_feature_importance"),
        _check(
            "insert_near_catboost",
            anchor_ok,
            f"insert index should be near {CATBOOST_CELL}",
        ),
        _check(
            "no_run_before_edit",
            names.index("run_cell") > names.index("edit_cell_by_index")
            if "run_cell" in names and "edit_cell_by_index" in names
            else True,
            "run should follow edit if present",
        ),
    ]
    return checks


def score_markdown_gpu(calls: list[ToolCallRecord], final: str) -> list[CheckResult]:
    names = _names(calls)
    md_insert = any(c.name == "creating_markdown_by_index" for c in calls)
    md_anchor = any(
        c.name == "creating_markdown_by_index"
        and int(c.args.get("index", 0)) == CATBOOST_CELL
        for c in calls
        if c.args.get("index") is not None
    )
    md_edit = any(
        c.name == "edit_cell_by_index"
        and _text_mentions(str(c.args.get("content") or ""), "gpu", "early stopping", "catboost")
        for c in calls
    )
    checks = [
        _check(
            "read_before_write",
            not names
            or next((i for i, n in enumerate(names) if n in BROWSER_TOOL_NAMES), 99)
            >= next((i for i, n in enumerate(names) if n.startswith("notebook_")), 0),
            f"order={names}",
        ),
        _check("creating_markdown", md_insert, str(names)),
        _check("markdown_above_catboost", md_anchor, f"index should be {CATBOOST_CELL}"),
        _check("edited_markdown_content", md_edit, "markdown should mention GPU/early stopping"),
    ]
    return checks


def score_notebook_stats(calls: list[ToolCallRecord], final: str) -> list[CheckResult]:
    names = _names(calls)
    status_tool = any(
        n in ("notebook_snapshot_status", "notebook_list_cells") for n in names
    )
    # Notebook has 62 code cells per snapshot_status from earlier run
    mentions_count = bool(re.search(r"\b(6[0-9]|[5-7][0-9]|78)\b", final or ""))
    checks = [
        _check("used_status_or_list", status_tool, str(names)),
        _check("no_write_tools", not any(n in BROWSER_TOOL_NAMES for n in names), str(names)),
        _check(
            "answer_has_cell_count",
            mentions_count or _text_mentions(final, "code cell", "cells"),
            "should report approximate cell/code counts",
        ),
    ]
    return checks


def score_symbol_placement(calls: list[ToolCallRecord], final: str) -> list[CheckResult]:
    names = _names(calls)
    used_recommend = "notebook_recommend_placement" in names or "notebook_find_symbol" in names
    checks = [
        _check(
            "used_placement_or_symbol",
            used_recommend,
            f"expected recommend/find; got {names}",
        ),
        _check(
            "mentions_insert_below",
            _text_mentions(final, "below", "insert", "cell 50", "50"),
            "should recommend placement relative to catboost",
        ),
        _check("no_write_without_ask", not any(n in BROWSER_TOOL_NAMES for n in names), str(names)),
    ]
    return checks


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "discovery_catboost",
        "title": "Locate CatBoost training cell",
        "prompt": (
            "Use tools to locate where catboost_model is trained in this notebook. "
            "Report the exact 1-based cell index and summarize what that cell does."
        ),
        "score_fn": score_discovery_catboost,
    },
    {
        "id": "correlation_analysis",
        "title": "Explain correlation analysis limitations",
        "prompt": (
            "Find where corr_with_price is computed in this notebook. "
            "Explain why Pearson correlation may be misleading given the mix of categorical "
            "and numeric features. Use read tools only — do not modify the notebook."
        ),
        "score_fn": score_correlation_analysis,
    },
    {
        "id": "dependency_order",
        "title": "Upstream dependencies before CatBoost",
        "prompt": (
            "Which cells must have been executed before catboost_model.fit can run? "
            "Use the dependency graph or cell neighbors tools and list upstream cells."
        ),
        "score_fn": score_dependency_order,
    },
    {
        "id": "insert_importance",
        "title": "Insert feature-importance cell below CatBoost",
        "prompt": (
            "Insert a new code cell below the CatBoost GPU training cell containing code "
            "to print the top 10 feature importances from catboost_model using feature_cols."
        ),
        "score_fn": score_insert_importance,
    },
    {
        "id": "markdown_gpu",
        "title": "Add markdown note above CatBoost cell",
        "prompt": (
            "Add a markdown cell above the CatBoost training cell explaining that it uses "
            "task_type GPU and early_stopping_rounds. Fill in the markdown content."
        ),
        "score_fn": score_markdown_gpu,
    },
    {
        "id": "notebook_stats",
        "title": "Notebook statistics (read-only)",
        "prompt": (
            "How many total cells and code cells are in this notebook? "
            "Use tools to verify — do not modify anything."
        ),
        "score_fn": score_notebook_stats,
    },
    {
        "id": "symbol_placement",
        "title": "Recommend where to add new CatBoost evaluation code",
        "prompt": (
            "I want to add code that evaluates catboost_model on the validation set. "
            "Where should I insert a new code cell? Use placement/symbol tools and explain."
        ),
        "score_fn": score_symbol_placement,
    },
]


def run_scenario(spec: dict, max_rounds: int, min_interval: float) -> ScenarioResult:
    t0 = time.perf_counter()
    calls, reasoning, final, err = run_agentic_scenario(
        prompt=spec["prompt"],
        max_rounds=max_rounds,
        min_interval=min_interval,
    )
    checks = spec["score_fn"](calls, final) if calls else []
    if err:
        if calls:
            checks.append(_check("llm_completion", False, err))
        else:
            checks = [_check("llm_run", False, err)]
    passed = sum(1 for c in checks if c.passed)
    max_pts = len(checks)
    return ScenarioResult(
        scenario_id=spec["id"],
        title=spec["title"],
        prompt=spec["prompt"],
        tool_calls=calls,
        reasoning_snippets=reasoning,
        final_text=final,
        checks=checks,
        score=float(passed),
        max_score=float(max_pts),
        error=err,
        elapsed_sec=time.perf_counter() - t0,
    )


def print_report(results: list[ScenarioResult], provider: str, model: str) -> None:
    print("=" * 76)
    print("LLM TOOL-CALL EVALUATION — Pakistan Housing Notebook")
    print("=" * 76)
    print(f"Provider : {provider}")
    print(f"Model    : {model}")
    print(f"Notebook : {URL}")
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
        print(f"   Prompt: {r.prompt[:100]}...")
        if r.reasoning_snippets:
            print(f"   Reasoning (round 1): {_safe(r.reasoning_snippets[0], 140)}...")
        print(f"   Tool sequence ({len(r.tool_calls)} calls):")
        for tc in r.tool_calls:
            args_brief = {k: v for k, v in tc.args.items() if k in ("url", "cell_index", "index", "query", "symbol")}
            ok = "ok" if tc.result_ok else "FAIL"
            print(f"     r{tc.round} [{ok}] {tc.name} {args_brief}")
        print("   Checks:")
        for c in r.checks:
            mark = "PASS" if c.passed else "FAIL"
            print(f"     [{mark}] {c.name}: {c.detail}")
        if r.final_text:
            print(f"   Final: {_safe(r.final_text, 200)}...")
        print()

    overall = 100.0 * total_score / total_max if total_max else 0.0
    print("=" * 76)
    print(f"OVERALL SCORE: {total_score:.0f}/{total_max:.0f} ({overall:.1f}%)")
    print("=" * 76)


def save_json_report(results: list[ScenarioResult], path: Path) -> None:
    payload = {
        "url": URL,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate LLM tool calls on Pakistan housing notebook")
    parser.add_argument(
        "--scenario",
        action="append",
        help="Run only these scenario ids (repeatable)",
    )
    parser.add_argument("--max-rounds", type=int, default=LLM_REACT_MAX_ROUNDS)
    parser.add_argument(
        "--json-out",
        default=str(REPO / "testing/host/data/meta/eval_pakistan_housing_llm.json"),
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=None,
        help="Seconds between LLM calls (default: provider rate limit)",
    )
    args = parser.parse_args()

    if not NOTEBOOK_FILE.is_file():
        print(f"Missing notebook JSON: {NOTEBOOK_FILE}", file=sys.stderr)
        return 1

    if _LLM_CLIENT is None:
        print("ERROR: No LLM API key configured. Set CEREBRAS_API_KEY or GEMINI_API_KEY in .env", file=sys.stderr)
        return 1

    min_interval = args.min_interval
    if min_interval is None:
        min_interval = react_min_interval_sec(LLM_PROVIDER)

    specs = SCENARIOS
    if args.scenario:
        wanted = set(args.scenario)
        specs = [s for s in SCENARIOS if s["id"] in wanted]
        if not specs:
            print(f"No matching scenarios. Available: {[s['id'] for s in SCENARIOS]}", file=sys.stderr)
            return 1

    print(f"Running {len(specs)} scenario(s) | provider={LLM_PROVIDER} model={LLM_MODEL}")
    print(f"Rate limit interval: {min_interval:.1f}s between LLM calls")
    print()

    results: list[ScenarioResult] = []
    for i, spec in enumerate(specs):
        if i > 0 and min_interval > 0:
            time.sleep(min_interval)
        print(f"--- Running scenario: {spec['id']} ---")
        results.append(run_scenario(spec, args.max_rounds, min_interval))

    print_report(results, LLM_PROVIDER, LLM_MODEL)
    save_json_report(results, Path(args.json_out))
    print(f"\nJSON report: {args.json_out}")

    failed = [r for r in results if r.error or (r.max_score and r.score < r.max_score)]
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
