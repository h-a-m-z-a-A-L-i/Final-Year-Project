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


def _cell_payload(cell: dict, *, include_output: bool, max_input: int, max_output: int) -> dict:
    inp = _truncate(cell.get("input") or "", max_input)
    out = ""
    if include_output and str(cell.get("type") or "code") == "code":
        out = _truncate(cell.get("output") or "", max_output)
    return {
        "index": cell.get("index"),
        "type": cell.get("type"),
        "input": inp,
        "output": out if include_output else None,
        "execution_order": cell.get("execution_order"),
        "execution_title": cell.get("execution_title"),
        "has_output": bool(str(cell.get("output") or "").strip()),
    }


def notebook_list_cells(args: dict) -> dict:
    """Compact index of all cells (types, previews, execution metadata)."""
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
            "execution_order": cell.get("execution_order"),
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
    max_in = int(args.get("max_input_chars") or DEFAULT_MAX_INPUT_CHARS)
    max_out = int(args.get("max_output_chars") or DEFAULT_MAX_OUTPUT_CHARS)
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
            hits.append({
                "cell_index": idx,
                "field": field,
                "line_preview": _truncate(line, 160, suffix="..."),
            })
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
    "notebook_snapshot_status": notebook_snapshot_status,
}

LOCAL_TOOL_NAMES = frozenset(LOCAL_TOOL_HANDLERS.keys())

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
        "description": "List all cells with index, type, short preview, and execution metadata.",
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
]


def register_local_tools(reg) -> None:
    for spec in LOCAL_TOOL_SPECS:
        name = spec["name"]
        reg.register(name, spec["schema"], spec["description"], LOCAL_TOOL_HANDLERS[name])
