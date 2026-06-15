"""
Read-only tools over local notebook JSON snapshots (live/ then persistent).

Browser tools in tools/ are not used here — the LLM queries scraped data only.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

try:
    from .config import MAX_CELL_OUTPUT_CHARS
    from .notebook_context import (
        _cell_by_index,
        _cells_from_data,
        build_dependency_graph,
        build_graph_list,
        load_notebook_snapshot,
    )
    from .symbol_graph import build_symbol_index
except Exception:
    from config import MAX_CELL_OUTPUT_CHARS
    from notebook_context import (
        _cell_by_index,
        _cells_from_data,
        build_dependency_graph,
        build_graph_list,
        load_notebook_snapshot,
    )
    from symbol_graph import build_symbol_index

DEFAULT_PREVIEW_CHARS = 120
DEFAULT_MAX_INPUT_CHARS = 6000
DEFAULT_MAX_OUTPUT_CHARS = int(MAX_CELL_OUTPUT_CHARS)
MAX_SEARCH_HITS = 20
MAX_BATCH_CELLS = 10
MAX_EXECUTED_CELLS = 15

try:
    from .config import QUERY_TOOL_MAX_INPUT_CHARS, QUERY_TOOL_MAX_OUTPUT_CHARS
except Exception:
    try:
        from config import QUERY_TOOL_MAX_INPUT_CHARS, QUERY_TOOL_MAX_OUTPUT_CHARS
    except Exception:
        QUERY_TOOL_MAX_INPUT_CHARS = 8000
        QUERY_TOOL_MAX_OUTPUT_CHARS = 6000

_PREVIEW_INPUT_HINT = re.compile(
    r"\b(head\s*\(|tail\s*\(|\.info\s*\(|describe\s*\(|\.shape|columns|dtypes|sample\s*\(|value_counts)",
    re.IGNORECASE,
)

_LOAD_CODE_HINT = re.compile(
    r"\b(read_csv|read_parquet|read_json|pd\.read_|load_dataset|/kaggle/input/)\b",
    re.IGNORECASE,
)


def _notebook_url(args: dict) -> str:
    return str(args.get("url") or args.get("tabUrl") or args.get("tab_url") or "").strip()


def _load(url: str) -> tuple[dict | None, str, list[dict]]:
    if not url:
        return None, "none", []
    data, source = load_notebook_snapshot(url)
    cells = _cells_from_data(data)
    return data, source, cells


def _err(msg: str, **extra) -> dict:
    return {"ok": False, "error": msg, **extra}


def _ok(**payload) -> dict:
    return {"ok": True, **payload}


def _truncate(text: str, max_chars: int, suffix: str = "\n...[truncated]") -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    keep = max(0, max_chars - len(suffix))
    return s[:keep] + suffix


def _query_output_cap(args: dict) -> int:
    return int(args.get("max_output_chars") or QUERY_TOOL_MAX_OUTPUT_CHARS)


def _query_input_cap(args: dict) -> int:
    return int(args.get("max_input_chars") or QUERY_TOOL_MAX_INPUT_CHARS)


def _cell_has_output(cell: dict) -> bool:
    return bool(str(cell.get("output") or "").strip())


def _is_data_preview_cell(cell: dict) -> bool:
    """Cell likely shows dataset contents (head/info) or has tabular output."""
    if str(cell.get("type") or "code") != "code":
        return False
    inp = str(cell.get("input") or "")
    out = str(cell.get("output") or "").strip()
    if not out:
        return False
    if _LOAD_CODE_HINT.search(inp) or _PREVIEW_INPUT_HINT.search(inp):
        return True
    if "|" in out[:500] and out.count("|") >= 3:
        return True
    if re.search(r"\b(columns|dtype|Non-Null|RangeIndex)\b", out, re.I):
        return True
    return False


def _cell_payload(cell: dict, *, include_output: bool, max_input: int, max_output: int) -> dict:
    inp = _truncate(cell.get("input") or "", max_input)
    out = ""
    raw_out = str(cell.get("output") or "")
    if include_output and str(cell.get("type") or "code") == "code":
        out = _truncate(raw_out, max_output)
    return {
        "index": cell.get("index"),
        "type": cell.get("type"),
        "input": inp,
        "output": out if include_output else None,
        "output_chars": len(raw_out) if include_output else 0,
        "output_truncated": include_output and len(raw_out) > max_output > 0,
        "has_output": _cell_has_output(cell),
    }


def notebook_list_cells(args: dict) -> dict:
    """Compact index of all cells (types and previews)."""
    url = _notebook_url(args)
    data, source, cells = _load(url)
    if not cells:
        return _err("No notebook snapshot found", url=url, snapshot=source)

    preview_len = int(args.get("preview_chars") or DEFAULT_PREVIEW_CHARS)
    items = []
    for cell in sorted(cells, key=lambda c: int(c.get("index", 0))):
        code = str(cell.get("input") or "")
        items.append({
            "index": cell.get("index"),
            "type": cell.get("type"),
            "preview": _truncate(code.replace("\n", " "), preview_len, suffix="..."),
            "has_output": bool(str(cell.get("output") or "").strip()),
        })
    return _ok(url=url, snapshot=source, cell_count=len(items), cells=items)


def notebook_graph_query(args: dict) -> dict:
    """Dependency graph: per-cell previews, upstream and downstream indices."""
    url = _notebook_url(args)
    if not url:
        return _err("url is required")
    graph = build_graph_list(url)
    _, source = load_notebook_snapshot(url)
    if not graph:
        return _err("No notebook snapshot found", url=url, snapshot=source)
    return _ok(url=url, snapshot=source, graph=graph)


def notebook_get_cell(args: dict) -> dict:
    """Full source (and optional output) for one cell by index."""
    url = _notebook_url(args)
    if not url:
        return _err("url is required")
    try:
        cell_index = int(args.get("cell_index") if args.get("cell_index") is not None else args.get("index"))
    except Exception:
        return _err("cell_index must be an integer")

    _, source, cells = _load(url)
    cell = _cell_by_index(cells, cell_index)
    if not cell:
        return _err(f"Cell {cell_index} not in snapshot", url=url, snapshot=source)

    include_output = bool(args.get("include_output", True))
    max_in = _query_input_cap(args)
    max_out = _query_output_cap(args)
    return _ok(
        url=url,
        snapshot=source,
        cell=_cell_payload(cell, include_output=include_output, max_input=max_in, max_output=max_out),
    )


def notebook_get_cells(args: dict) -> dict:
    """Fetch multiple cells by index list (budget-limited batch)."""
    url = _notebook_url(args)
    if not url:
        return _err("url is required")

    raw_indices = args.get("cell_indices") or args.get("indices") or []
    if not isinstance(raw_indices, list) or not raw_indices:
        return _err("cell_indices must be a non-empty list of integers")

    try:
        indices = [int(i) for i in raw_indices[:MAX_BATCH_CELLS]]
    except Exception:
        return _err("cell_indices must contain integers")

    _, source, cells = _load(url)
    include_output = bool(args.get("include_output", False))
    max_in = int(args.get("max_input_chars") or 2000)
    max_out = int(args.get("max_output_chars") or 1200)
    found = []
    missing = []
    for idx in indices:
        cell = _cell_by_index(cells, idx)
        if cell:
            found.append(
                _cell_payload(cell, include_output=include_output, max_input=max_in, max_output=max_out)
            )
        else:
            missing.append(idx)
    if not found:
        return _err("No requested cells in snapshot", url=url, missing=missing)
    return _ok(url=url, snapshot=source, cells=found, missing=missing or None)


def notebook_find_symbol(args: dict) -> dict:
    """Find where a Python name is defined (assignment, def, class, import)."""
    url = _notebook_url(args)
    symbol = str(args.get("symbol") or args.get("name") or "").strip()
    if not url:
        return _err("url is required")
    if not symbol:
        return _err("symbol is required")

    _, source, cells = _load(url)
    if not cells:
        return _err("No notebook snapshot found", url=url, snapshot=source)

    index = build_symbol_index(cells)
    sites = index.defs.get(symbol, [])
    if not sites:
        return _ok(url=url, snapshot=source, symbol=symbol, definitions=[], message="No definitions found")

    definitions = [
        {
            "cell_index": s.cell_index,
            "kind": s.kind,
            "lines": f"{s.start_line}-{s.end_line}",
            "snippet": s.snippet,
        }
        for s in sorted(sites, key=lambda x: x.cell_index)
    ]
    latest = max(sites, key=lambda s: s.cell_index)
    return _ok(
        url=url,
        snapshot=source,
        symbol=symbol,
        definitions=definitions,
        latest_definition_cell=latest.cell_index,
        recommended_insert_below=latest.cell_index,
    )


def notebook_search(args: dict) -> dict:
    """Search cell inputs/outputs for a substring or regex pattern."""
    url = _notebook_url(args)
    query = str(args.get("query") or args.get("pattern") or "").strip()
    if not url:
        return _err("url is required")
    if not query:
        return _err("query is required")

    use_regex = bool(args.get("regex", False))
    try:
        pattern = re.compile(query) if use_regex else None
    except re.error as e:
        return _err(f"Invalid regex: {e}")

    limit = min(int(args.get("limit") or MAX_SEARCH_HITS), MAX_SEARCH_HITS)
    search_output = bool(args.get("search_output", True))

    _, source, cells = _load(url)
    hits = []
    q_lower = query.lower()
    for cell in sorted(cells, key=lambda c: int(c.get("index", 0))):
        idx = cell.get("index")
        for field in ("input", "output") if search_output else ("input",):
            text = str(cell.get(field) or "")
            if not text:
                continue
            matched = bool(pattern.search(text)) if pattern else (q_lower in text.lower())
            if not matched:
                continue
            line = text.splitlines()[0] if text else ""
            hit: dict = {
                "cell_index": idx,
                "field": field,
                "line_preview": _truncate(line, 160, suffix="..."),
            }
            if bool(args.get("include_field_text", search_output)):
                cap = int(args.get("max_field_chars") or _query_output_cap(args))
                hit["field_text"] = _truncate(text, cap)
            hits.append(hit)
            break
        if len(hits) >= limit:
            break

    return _ok(url=url, snapshot=source, query=query, hit_count=len(hits), hits=hits)


def notebook_cell_neighbors(args: dict) -> dict:
    """Upstream/downstream dependency indices for one cell."""
    url = _notebook_url(args)
    if not url:
        return _err("url is required")
    try:
        cell_index = int(args.get("cell_index") if args.get("cell_index") is not None else args.get("index"))
    except Exception:
        return _err("cell_index must be an integer")

    _, source, cells = _load(url)
    if not cells:
        return _err("No notebook snapshot found", url=url, snapshot=source)

    tracker, deps, reverse_deps = build_dependency_graph(cells)
    if cell_index not in tracker.symbol_table and not _cell_by_index(cells, cell_index):
        return _err(f"Cell {cell_index} not in snapshot", url=url)

    direct_up = list(deps.get(cell_index, []))
    direct_down = sorted(reverse_deps.get(cell_index, []))
    return _ok(
        url=url,
        snapshot=source,
        cell_index=cell_index,
        direct_upstream=direct_up,
        direct_downstream=direct_down,
        suggested_run_order=sorted(set(direct_up + [cell_index])),
    )


def extract_symbols_from_text(text: str) -> list[str]:
    """Pull likely Python identifiers from user prompt (e.g. model_df)."""
    symbols: set[str] = set()
    for m in re.finditer(r"`([^`]+)`", str(text or "")):
        candidate = m.group(1).strip()
        if re.match(r"^[A-Za-z_]\w*$", candidate):
            symbols.add(candidate)
    for m in re.finditer(r"\b([A-Za-z_][\w]*(?:_df|_data|DataFrame)?|df|model_df)\b", str(text or ""), re.I):
        name = m.group(1)
        if name.lower() not in {"for", "if", "in", "and", "the", "check"}:
            symbols.add(name)
    return sorted(symbols, key=len, reverse=True)[:6]


def notebook_recommend_placement(args: dict) -> dict:
    """
    Recommend inserting a NEW code cell below the cell that defines required symbol(s).
    """
    url = _notebook_url(args)
    if not url:
        return _err("url is required")

    raw_symbols = args.get("symbols") or args.get("symbol")
    if isinstance(raw_symbols, str):
        symbols = [raw_symbols.strip()] if raw_symbols.strip() else []
    elif isinstance(raw_symbols, list):
        symbols = [str(s).strip() for s in raw_symbols if str(s).strip()]
    else:
        symbols = []

    if not symbols:
        return _err("symbol or symbols list is required (e.g. model_df)")

    _, source, cells = _load(url)
    if not cells:
        return _err("No notebook snapshot found", url=url, snapshot=source)

    try:
        from .notebook_context import _transitive_deps, build_dependency_graph
    except Exception:
        from notebook_context import _transitive_deps, build_dependency_graph

    tracker, deps, _reverse = build_dependency_graph(cells)
    index = build_symbol_index(cells)

    symbol_plans = []
    anchor_cell: int | None = None

    for sym in symbols:
        sites = index.defs.get(sym, [])
        if not sites:
            symbol_plans.append({"symbol": sym, "found": False})
            continue
        latest = max(sites, key=lambda s: s.cell_index)
        def_cell = int(latest.cell_index)
        if anchor_cell is None or def_cell > anchor_cell:
            anchor_cell = def_cell
        up = _transitive_deps(tracker, def_cell) if def_cell is not None else []
        symbol_plans.append({
            "symbol": sym,
            "found": True,
            "defined_in_cell": def_cell,
            "definition_kind": latest.kind,
            "definition_snippet": latest.snippet,
            "upstream_cells": up,
        })

    if anchor_cell is None:
        return _ok(
            url=url,
            snapshot=source,
            symbols=symbol_plans,
            recommendation=None,
            message="No definitions found. Ask user which cell creates the data, or refresh snapshot.",
        )

    upstream_union: set[int] = set()
    for plan in symbol_plans:
        if plan.get("found"):
            upstream_union.update(plan.get("upstream_cells") or [])
    run_order = sorted(upstream_union | {anchor_cell})

    return _ok(
        url=url,
        snapshot=source,
        symbols=symbol_plans,
        recommendation={
            "action": "insert_new_code_cell",
            "insert_below_cell_index": anchor_cell,
            "instruction": (
                f"In the notebook UI: click Cell [{anchor_cell}], then **Insert Code Cell Below** "
                f"(add a new cell — do not hunt for unrelated empty cells elsewhere)."
            ),
            "run_order": run_order + ["<new cell below {0}>".format(anchor_cell)],
            "avoid": [
                "Do not recommend reusing a distant 'empty' cell far below the definition unless it is immediately after the defining cell.",
                "Do not overwrite markdown/comment-only cells unless the user asked to edit that specific cell.",
                "Do not suggest Cell [1] unless the user will re-run the entire notebook from the top.",
            ],
        },
    )


def notebook_overview(args: dict) -> dict:
    """Structured notebook overview: markdown intros, data-load cells, optional search hits."""
    url = _notebook_url(args)
    if not url:
        return _err("url is required")

    _, source, cells = _load(url)
    if not cells:
        return _err("No notebook snapshot found", url=url, snapshot=source)

    include_markdown = bool(args.get("include_markdown", True))
    max_md = int(args.get("max_markdown_chars") or 1200)
    max_in = _query_input_cap(args)
    max_out = _query_output_cap(args)

    markdown_cells: list[dict] = []
    data_load_cells: list[dict] = []
    preview_cells: list[dict] = []
    seen_indices: set[int] = set()
    for cell in sorted(cells, key=lambda c: int(c.get("index", 0))):
        ctype = str(cell.get("type") or "code")
        inp = str(cell.get("input") or "")
        try:
            idx = int(cell.get("index", 0))
        except Exception:
            idx = 0
        if include_markdown and ctype == "markdown" and inp.strip():
            markdown_cells.append({
                "index": idx,
                "text": _truncate(inp, max_md),
            })
        if ctype == "code" and (_LOAD_CODE_HINT.search(inp) or "dataset" in inp.lower()):
            payload = _cell_payload(cell, include_output=True, max_input=max_in, max_output=max_out)
            data_load_cells.append(payload)
            seen_indices.add(idx)
        if _is_data_preview_cell(cell) and idx not in seen_indices:
            preview_cells.append(
                _cell_payload(cell, include_output=True, max_input=max_in, max_output=max_out)
            )
            seen_indices.add(idx)

    search_terms = args.get("search_terms") or []
    search_hits: list[dict] = []
    if isinstance(search_terms, list):
        for term in search_terms[:4]:
            term = str(term or "").strip()
            if not term:
                continue
            hit = notebook_search({"url": url, "query": term, "limit": 6})
            if hit.get("ok"):
                search_hits.append({"query": term, "hits": hit.get("hits") or []})

    code_count = sum(1 for c in cells if str(c.get("type") or "code") == "code")
    with_output = sum(1 for c in cells if str(c.get("output") or "").strip())

    return _ok(
        url=url,
        snapshot=source,
        summary={
            "cell_count": len(cells),
            "code_cells": code_count,
            "cells_with_output": with_output,
            "markdown_cells": len(markdown_cells),
            "data_load_cells": len(data_load_cells),
        },
        markdown_cells=markdown_cells[:8],
        data_load_cells=data_load_cells[:6],
        preview_cells=preview_cells[:10],
        search_hits=search_hits or None,
    )


def notebook_executed_cells(args: dict) -> dict:
    """Code cells with non-empty output: index, full input, and full output (for LLM evidence)."""
    url = _notebook_url(args)
    if not url:
        return _err("url is required")

    _, source, cells = _load(url)
    if not cells:
        return _err("No notebook snapshot found", url=url, snapshot=source)

    max_cells = min(int(args.get("max_cells") or MAX_EXECUTED_CELLS), MAX_EXECUTED_CELLS)
    max_in = _query_input_cap(args)
    max_out = _query_output_cap(args)
    preview_only = bool(args.get("preview_only", False))

    executed: list[dict] = []
    for cell in sorted(cells, key=lambda c: int(c.get("index", 0))):
        if str(cell.get("type") or "code") != "code" or not _cell_has_output(cell):
            continue
        if preview_only and not _is_data_preview_cell(cell):
            continue
        executed.append(
            _cell_payload(
                cell,
                include_output=True,
                max_input=max_in,
                max_output=max_out,
            )
        )
        if len(executed) >= max_cells:
            break

    if not executed:
        return _ok(
            url=url,
            snapshot=source,
            cell_count=0,
            cells=[],
            message="No code cells with output in snapshot.",
        )
    return _ok(
        url=url,
        snapshot=source,
        cell_count=len(executed),
        cells=executed,
    )


def notebook_kernel_state(args: dict) -> dict:
    """
    Kernel scenario (off / fresh / reload) and which cells ran since the session started.
    Use before analysis cells that depend on df or other upstream variables.
    """
    url = _notebook_url(args)
    if not url:
        return _err("url is required")

    _, source, cells = _load(url)
    if not cells:
        return _err("No notebook snapshot found", url=url, snapshot=source)

    try:
        from .kernel_session import analyze_kernel_session
    except Exception:
        from kernel_session import analyze_kernel_session

    target_cell = args.get("cell_index")
    if target_cell is not None:
        try:
            target_cell = int(target_cell)
        except (TypeError, ValueError):
            return _err("cell_index must be an integer")

    symbols = args.get("symbols")
    if isinstance(symbols, str):
        symbols = [symbols]
    if not symbols and args.get("symbol"):
        symbols = [str(args.get("symbol"))]
    if not symbols and args.get("user_prompt"):
        symbols = extract_symbols_from_text(str(args.get("user_prompt")))

    report = analyze_kernel_session(
        url,
        cells,
        target_cell_index=target_cell,
        symbols=list(symbols) if symbols else None,
    )
    report["ok"] = True
    report["snapshot"] = source
    return report


def notebook_kernel_execution(args: dict) -> dict:
    """
    Full kernel scenario + per-cell execution board (mode-aware).
    OFF: execution_order only. FRESH: lifecycle titles. RELOAD: preserved execution.
    """
    url = _notebook_url(args)
    if not url:
        return _err("url is required")

    data, source, cells = _load(url)
    if not cells:
        return _err("No notebook snapshot found", url=url, snapshot=source)

    try:
        from .kernel_execution_policy import build_kernel_execution_report, normalize_scenario
        from .kernel_session import get_live_kernel_context, _load_execution_notebook_state
    except Exception:
        from kernel_execution_policy import build_kernel_execution_report, normalize_scenario
        from kernel_session import get_live_kernel_context, _load_execution_notebook_state

    live = get_live_kernel_context()
    persisted = _load_execution_notebook_state(url)
    scenario = normalize_scenario(
        (data or {}).get("kernelScenario")
        or live.get("kernelScenario")
        or persisted.get("last_kernel_scenario")
        or "unknown"
    )

    report = build_kernel_execution_report(
        url=url,
        cells=cells,
        kernel_scenario=scenario,
        kernel_session_started_at=persisted.get("kernel_session_started_at"),
        kernel_session_stopped_at=persisted.get("kernel_session_stopped_at"),
    )
    report["snapshot"] = source
    return report


def notebook_snapshot_status(args: dict) -> dict:
    """Report whether live/persistent JSON exists and basic stats."""
    url = _notebook_url(args)
    if not url:
        return _err("url is required")
    data, source, cells = _load(url)
    if not cells:
        return _ok(url=url, snapshot="none", available=False, cell_count=0)
    code_cells = sum(1 for c in cells if str(c.get("type") or "code") == "code")
    with_output = sum(1 for c in cells if str(c.get("output") or "").strip())
    indices = [int(c.get("index", 0)) for c in cells]
    return _ok(
        url=url,
        snapshot=source,
        available=True,
        cell_count=len(cells),
        code_cells=code_cells,
        cells_with_output=with_output,
        index_min=min(indices) if indices else None,
        index_max=max(indices) if indices else None,
    )


LOCAL_TOOL_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "notebook_list_cells": notebook_list_cells,
    "notebook_graph_query": notebook_graph_query,
    "notebook_get_cell": notebook_get_cell,
    "notebook_get_cells": notebook_get_cells,
    "notebook_find_symbol": notebook_find_symbol,
    "notebook_search": notebook_search,
    "notebook_cell_neighbors": notebook_cell_neighbors,
    "notebook_recommend_placement": notebook_recommend_placement,
    "notebook_overview": notebook_overview,
    "notebook_executed_cells": notebook_executed_cells,
    "notebook_snapshot_status": notebook_snapshot_status,
}

# Host/monitor only — not exposed to the LLM until execution tracking is stable.
INTERNAL_KERNEL_TOOL_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "notebook_kernel_state": notebook_kernel_state,
    "notebook_kernel_execution": notebook_kernel_execution,
}

LOCAL_TOOL_NAMES = frozenset(LOCAL_TOOL_HANDLERS.keys())
LLM_LOCAL_TOOL_NAMES = LOCAL_TOOL_NAMES

_URL_SCHEMA = {"type": "string"}
_CELL_SCHEMA = {"type": "integer"}

LOCAL_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "notebook_snapshot_status",
        "schema": {"type": "object", "properties": {"url": _URL_SCHEMA}, "required": ["url"]},
        "description": "Check if a local notebook JSON snapshot exists (live or persistent) and return counts.",
    },
    {
        "name": "notebook_list_cells",
        "schema": {
            "type": "object",
            "properties": {
                "url": _URL_SCHEMA,
                "preview_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
        "description": "List all cells with index, type, short preview, and whether output exists.",
    },
    {
        "name": "notebook_graph_query",
        "schema": {"type": "object", "properties": {"url": _URL_SCHEMA}, "required": ["url"]},
        "description": "Return dependency graph nodes: cell index, preview, upstream/downstream indices.",
    },
    {
        "name": "notebook_get_cell",
        "schema": {
            "type": "object",
            "properties": {
                "url": _URL_SCHEMA,
                "cell_index": _CELL_SCHEMA,
                "include_output": {"type": "boolean"},
                "max_input_chars": {"type": "integer"},
                "max_output_chars": {"type": "integer"},
            },
            "required": ["url", "cell_index"],
        },
        "description": "Get full source and optional output for one cell from local JSON.",
    },
    {
        "name": "notebook_get_cells",
        "schema": {
            "type": "object",
            "properties": {
                "url": _URL_SCHEMA,
                "cell_indices": {"type": "array", "items": _CELL_SCHEMA},
                "include_output": {"type": "boolean"},
            },
            "required": ["url", "cell_indices"],
        },
        "description": "Batch-fetch up to 10 cells by index from local JSON.",
    },
    {
        "name": "notebook_find_symbol",
        "schema": {
            "type": "object",
            "properties": {"url": _URL_SCHEMA, "symbol": {"type": "string"}},
            "required": ["url", "symbol"],
        },
        "description": "Find definition sites for a Python symbol (e.g. model_df) across the notebook.",
    },
    {
        "name": "notebook_search",
        "schema": {
            "type": "object",
            "properties": {
                "url": _URL_SCHEMA,
                "query": {"type": "string"},
                "regex": {"type": "boolean"},
                "search_output": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
            "required": ["url", "query"],
        },
        "description": "Search cell inputs/outputs in local JSON for text or regex.",
    },
    {
        "name": "notebook_cell_neighbors",
        "schema": {
            "type": "object",
            "properties": {"url": _URL_SCHEMA, "cell_index": _CELL_SCHEMA},
            "required": ["url", "cell_index"],
        },
        "description": "Get direct upstream/downstream cells and suggested run order for one cell.",
    },
    {
        "name": "notebook_recommend_placement",
        "schema": {
            "type": "object",
            "properties": {
                "url": _URL_SCHEMA,
                "symbol": {"type": "string"},
                "symbols": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["url"],
        },
        "description": (
            "Recommend inserting a NEW code cell below the cell where symbol(s) are defined; "
            "includes run order and what to avoid (distant empty cells)."
        ),
    },
    {
        "name": "notebook_overview",
        "schema": {
            "type": "object",
            "properties": {
                "url": _URL_SCHEMA,
                "include_markdown": {"type": "boolean"},
                "search_terms": {"type": "array", "items": {"type": "string"}},
                "max_markdown_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
        "description": (
            "Structured overview: markdown intro cells, data-loading code cells, and optional search hits."
        ),
    },
    {
        "name": "notebook_executed_cells",
        "schema": {
            "type": "object",
            "properties": {
                "url": _URL_SCHEMA,
                "max_cells": {"type": "integer"},
                "preview_only": {"type": "boolean"},
                "max_input_chars": {"type": "integer"},
                "max_output_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
        "description": (
            "Return code cells that have captured output, each with index, input, and output text."
        ),
    },
]


def register_local_tools(reg) -> None:
    for spec in LOCAL_TOOL_SPECS:
        name = spec["name"]
        reg.register(name, spec["schema"], spec["description"], LOCAL_TOOL_HANDLERS[name])


def run_internal_kernel_tool(name: str, args: dict) -> dict:
    """Run kernel execution tools (host/monitor only, not LLM-facing)."""
    try:
        from .execution_metadata import enabled as _exec_meta_on
    except Exception:
        from execution_metadata import enabled as _exec_meta_on
    if not _exec_meta_on():
        return _err(
            "Kernel execution metadata is disabled",
            disabled=True,
            hint="Set KERNEL_EXECUTION_METADATA_ENABLED=1 to re-enable",
        )
    handler = INTERNAL_KERNEL_TOOL_HANDLERS.get(name)
    if not handler:
        return _err(f"Unknown internal kernel tool: {name}")
    return handler(args)
