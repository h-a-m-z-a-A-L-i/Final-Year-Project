"""Mode-aware notebook query planning and host-side prefetch (Ask/Code/Agentic).

The notebook JSON snapshot is the queryable codebase. This module picks which
read tools to run from mode + user intent, executes them once per turn, and
formats compact evidence for the LLM tail (no ReAct loop in Ask/Code).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_DATASET_HINT = re.compile(
    r"\b(dataset|read_csv|read_parquet|load\s+data|what\s+is.*about|describe\s+the\s+data|"
    r"zameen|kaggle/input|\.csv|dataframe|model_df)\b",
    re.IGNORECASE,
)

_LOAD_CODE_HINT = re.compile(
    r"\b(read_csv|read_parquet|read_json|pd\.read_|load_dataset|/kaggle/input/)\b",
    re.IGNORECASE,
)


def _query_tool_caps() -> dict[str, int]:
    try:
        from .config import QUERY_TOOL_MAX_INPUT_CHARS, QUERY_TOOL_MAX_OUTPUT_CHARS
    except Exception:
        from config import QUERY_TOOL_MAX_INPUT_CHARS, QUERY_TOOL_MAX_OUTPUT_CHARS
    return {
        "max_input_chars": int(QUERY_TOOL_MAX_INPUT_CHARS),
        "max_output_chars": int(QUERY_TOOL_MAX_OUTPUT_CHARS),
    }


@dataclass
class QueryStep:
    tool: str
    args: dict[str, Any]
    reason: str = ""


@dataclass
class QueryResult:
    tool: str
    reason: str
    payload: dict[str, Any]
    ok: bool = True
    error: str = ""


def _classify_intent(mode: str, prompt: str) -> str:
    try:
        from .prompt_engineering import classify_ask_intent
    except Exception:
        from prompt_engineering import classify_ask_intent
    intent = classify_ask_intent(prompt)
    if intent == "general" and _DATASET_HINT.search(prompt or ""):
        return "dataset"
    if str(mode or "").lower() == "code" and _PLACEMENT_HINT(prompt):
        return "placement"
    return intent


def _PLACEMENT_HINT(text: str) -> bool:
    try:
        from .prompt_engineering import _PLACEMENT_HINT_PATTERN
    except Exception:
        from prompt_engineering import _PLACEMENT_HINT_PATTERN
    return bool(_PLACEMENT_HINT_PATTERN.search(text or ""))


def _extract_symbols(prompt: str) -> list[str]:
    try:
        from .local_notebook_tools import extract_symbols_from_text
    except Exception:
        from local_notebook_tools import extract_symbols_from_text
    return extract_symbols_from_text(prompt)


def _search_queries_for_prompt(prompt: str) -> list[str]:
    text = str(prompt or "").lower()
    queries: list[str] = []
    if _DATASET_HINT.search(text):
        for term in ("read_csv", "dataset", "kaggle/input", "model_df", "pd.read"):
            if term.replace("_", " ") in text or term in text:
                queries.append(term)
        if not queries:
            queries = ["read_csv", "dataset"]
    if re.search(r"\b(error|traceback|failed|exception)\b", text, re.I):
        queries.append("Traceback")
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        k = q.lower()
        if k not in seen:
            seen.add(k)
            out.append(q)
    return out[:4]


def tools_allowed_for_mode(mode: str, *, agentic: bool) -> frozenset[str]:
    """Read tools exposed per mode (browser tools handled separately in agentic)."""
    try:
        from .local_notebook_tools import LOCAL_TOOL_NAMES
    except Exception:
        from local_notebook_tools import LOCAL_TOOL_NAMES
    base = set(LOCAL_TOOL_NAMES)
    if agentic:
        return frozenset(base)
    mode = str(mode or "ask").lower()
    if mode == "ask":
        return frozenset(
            n
            for n in base
            if n
            in {
                "notebook_snapshot_status",
                "notebook_list_cells",
                "notebook_overview",
                "notebook_executed_cells",
                "notebook_get_cell",
                "notebook_get_cells",
                "notebook_search",
                "notebook_find_symbol",
                "notebook_cell_neighbors",
                "notebook_graph_query",
                "notebook_recommend_placement",
            }
        )
    if mode == "code":
        return frozenset(base)
    return frozenset(base)


def build_query_plan(
    *,
    mode: str,
    prompt: str,
    url: str,
    cell_index: int | None = None,
    static_cache: bool = False,
    agentic: bool = False,
) -> list[QueryStep]:
    """Choose read-tool calls for this turn (host executes; Ask/Code do not ReAct)."""
    if agentic:
        return []

    mode = str(mode or "ask").lower()
    allowed = tools_allowed_for_mode(mode, agentic=False)
    intent = _classify_intent(mode, prompt)
    steps: list[QueryStep] = []

    caps = _query_tool_caps()

    def add(tool: str, args: dict, reason: str) -> None:
        if tool in allowed:
            merged = {**caps, **args}
            steps.append(QueryStep(tool, merged, reason=reason))

    if cell_index is not None:
        add(
            "notebook_get_cell",
            {"url": url, "cell_index": int(cell_index), "include_output": True},
            reason=f"{intent}: target cell",
        )
        if intent in ("explain", "error", "dependency", "review"):
            add(
                "notebook_cell_neighbors",
                {"url": url, "cell_index": int(cell_index)},
                reason=f"{intent}: upstream/downstream",
            )

    symbols = _extract_symbols(prompt)
    if intent == "placement" or (mode == "code" and symbols):
        if symbols:
            add(
                "notebook_recommend_placement",
                {"url": url, "symbols": symbols},
                reason="placement: where to insert code",
            )
            for sym in symbols[:2]:
                add(
                    "notebook_find_symbol",
                    {"url": url, "symbol": sym},
                    reason=f"placement: locate `{sym}`",
                )
        elif intent == "placement":
            add(
                "notebook_list_cells",
                {"url": url, "preview_chars": 100},
                reason="placement: cell index overview",
            )

    if intent == "dependency" and cell_index is None:
        add("notebook_graph_query", {"url": url}, reason="dependency: full graph")

    if intent == "dataset":
        add(
            "notebook_executed_cells",
            {"url": url, "preview_only": False},
            reason="dataset: all cells with execution output (input+output+index)",
        )
        add(
            "notebook_overview",
            {
                "url": url,
                "search_terms": _search_queries_for_prompt(prompt),
                "include_markdown": True,
            },
            reason="dataset: markdown + load/preview cells",
        )
    elif intent in ("general", "review") or not static_cache:
        add(
            "notebook_overview",
            {
                "url": url,
                "search_terms": _search_queries_for_prompt(prompt),
                "include_markdown": True,
            },
            reason="overview: markdown + data-load cells + search",
        )
        if intent == "general":
            add(
                "notebook_executed_cells",
                {"url": url, "preview_only": True},
                reason="general: executed preview cells with output",
            )

    if intent == "error" and cell_index is None:
        add(
            "notebook_search",
            {"url": url, "query": "Traceback", "search_output": True, "limit": 8},
            reason="error: find cells with tracebacks",
        )

    for query in _search_queries_for_prompt(prompt):
        add(
            "notebook_search",
            {
                "url": url,
                "query": query,
                "limit": 10,
                "include_field_text": True,
                "search_output": True,
            },
            reason=f"search: `{query}`",
        )

    if intent == "explain" and cell_index is None:
        add(
            "notebook_list_cells",
            {"url": url, "preview_chars": 120},
            reason="explain: locate cell by preview",
        )

    if not static_cache and not steps:
        add("notebook_snapshot_status", {"url": url}, reason="fallback: snapshot status")
        add("notebook_list_cells", {"url": url, "preview_chars": 80}, reason="fallback: cell index")

    # Dedupe by (tool, json args)
    seen: set[str] = set()
    unique: list[QueryStep] = []
    for step in steps:
        key = step.tool + ":" + json.dumps(step.args, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(step)
    return unique


def execute_query_plan(registry, steps: list[QueryStep]) -> list[QueryResult]:
    results: list[QueryResult] = []
    for step in steps:
        try:
            payload = registry.call(step.tool, dict(step.args))
            ok = bool(isinstance(payload, dict) and payload.get("ok"))
            err = str(payload.get("error") or "") if isinstance(payload, dict) else ""
            results.append(
                QueryResult(
                    tool=step.tool,
                    reason=step.reason,
                    payload=payload if isinstance(payload, dict) else {"raw": payload},
                    ok=ok,
                    error=err,
                )
            )
        except Exception as exc:
            results.append(
                QueryResult(
                    tool=step.tool,
                    reason=step.reason,
                    payload={},
                    ok=False,
                    error=str(exc),
                )
            )
    return results


def format_query_results_block(results: list[QueryResult]) -> str:
    if not results:
        return ""
    parts = [
        "## Notebook query results (host prefetch)",
        "Evidence from local snapshot tools for this turn. Prefer this over guessing.",
    ]
    for row in results:
        header = f"### {row.tool}"
        if row.reason:
            header += f" — {row.reason}"
        parts.append(header)
        if row.ok:
            parts.append("```json")
            parts.append(json.dumps(row.payload, ensure_ascii=False, indent=2))
            parts.append("```")
        else:
            parts.append(f"Error: {row.error or 'tool failed'}")
    return "\n".join(parts)


def prefetch_notebook_queries(
    *,
    registry,
    mode: str,
    prompt: str,
    url: str,
    cell_index: int | None = None,
    static_cache: bool = False,
    agentic: bool = False,
) -> tuple[str, list[QueryResult]]:
    """Plan, run, and format read-tool evidence for the current user turn."""
    plan = build_query_plan(
        mode=mode,
        prompt=prompt,
        url=url,
        cell_index=cell_index,
        static_cache=static_cache,
        agentic=agentic,
    )
    results = execute_query_plan(registry, plan)
    block = format_query_results_block(results)
    return block, results


def load_mode_query_prompt(mode: str) -> str:
    """Mode-specific guidance for using notebook query tools."""
    try:
        from .prompt_engineering import normalize_mode, _read_text, TOOL_PROMPTS_DIR
    except Exception:
        from prompt_engineering import normalize_mode, _read_text, TOOL_PROMPTS_DIR
    mode = normalize_mode(mode)
    path = TOOL_PROMPTS_DIR / f"query_tools_{mode}.txt"
    body = _read_text(path)
    if body:
        return body
    return _read_text(TOOL_PROMPTS_DIR / "local_read_tools.txt")
