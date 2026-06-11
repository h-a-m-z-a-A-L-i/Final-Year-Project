"""Mode-specific notebook context assembly for chat (in-repo, no external ContextBuilder)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import MAX_CELL_OUTPUT_CHARS, MAX_NOTEBOOK_CONTEXT_CHARS
    from .extract_dependencies import DependencyTracker
    from .persistence_helpers import get_safe_filename, read_json_file
    from .prompt_engineering import normalize_mode
    from .prompt_utils import _extract_cell_number
except Exception:
    from config import MAX_CELL_OUTPUT_CHARS, MAX_NOTEBOOK_CONTEXT_CHARS
    from extract_dependencies import DependencyTracker
    from persistence_helpers import get_safe_filename, read_json_file
    from prompt_engineering import normalize_mode
    from prompt_utils import _extract_cell_number


def _scraped_dir() -> Path:
    try:
        from . import config as _config
        return _config.SCRAPED_DIR
    except Exception:
        try:
            from config import SCRAPED_DIR
            return SCRAPED_DIR
        except Exception:
            from testing.host.config import SCRAPED_DIR
            return SCRAPED_DIR

TOOL_FIRST_MODES = frozenset({"ask", "code"})


@dataclass
class ContextPack:
    text: str = ""
    coverage: str = "none"  # full | partial | none
    snapshot: str = "none"  # live | persistent | none
    cell_index: int | None = None
    kernel_scenario: str = "unknown"
    manifest: dict = field(default_factory=dict)


def _cells_from_data(data: dict | None) -> list[dict]:
    if not isinstance(data, dict):
        return []
    cells = data.get("cells")
    if not isinstance(cells, list):
        return []
    try:
        from .cell_index import normalize_notebook_cells
    except Exception:
        from cell_index import normalize_notebook_cells
    normalize_notebook_cells(cells)
    return cells


def load_notebook_snapshot(url: str) -> tuple[dict | None, str]:
    """Return (data, source) preferring live then persistent."""
    if not url:
        return None, "none"
    scraped = _scraped_dir()
    filename = get_safe_filename(url)
    live_path = scraped / "live" / filename
    persistent_path = scraped / "persistent" / filename

    live_data = read_json_file(live_path) if live_path.is_file() else None
    if _cells_from_data(live_data):
        return live_data, "live"

    persistent_data = read_json_file(persistent_path) if persistent_path.is_file() else None
    if _cells_from_data(persistent_data):
        return persistent_data, "persistent"

    legacy_path = scraped / filename
    legacy_data = read_json_file(legacy_path) if legacy_path.is_file() else None
    if _cells_from_data(legacy_data):
        return legacy_data, "persistent"

    return None, "none"


def _snapshot_mtime(url: str, source: str) -> str | None:
    if not url or source == "none":
        return None
    filename = get_safe_filename(url)
    scraped = _scraped_dir()
    if source == "live":
        path = scraped / "live" / filename
    else:
        path = scraped / "persistent" / filename
    if not path.is_file():
        return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        return None


def get_freshness_metadata(url: str, bot_state: dict | None = None) -> dict[str, Any]:
    bot_state = bot_state or {}
    scenario = str(bot_state.get("kernelScenario") or bot_state.get("scenario") or "unknown")
    editor_loading = scenario == "editor_loading" or bool(
        (bot_state.get("kernelState") or {}).get("editorLoading")
    )
    _, source = load_notebook_snapshot(url)
    return {
        "kernel_scenario": scenario,
        "editor_loading": editor_loading,
        "snapshot": source,
        "snapshot_mtime": _snapshot_mtime(url, source),
    }


def build_graph_list(url: str) -> list[dict]:
    """Dependency graph nodes for UI panel (uses live then persistent snapshot)."""
    data, _ = load_notebook_snapshot(url)
    cells = _cells_from_data(data)
    if not cells:
        return []

    _, deps, reverse_deps = build_dependency_graph(cells)
    graph: list[dict] = []
    for cell in cells:
        if str(cell.get("type") or "code") != "code":
            continue
        try:
            idx = int(cell.get("index", 0))
        except Exception:
            continue
        code = str(cell.get("input") or "")
        graph.append({
            "cell_number": idx,
            "input_preview": code[:120],
            "dependencies": list(deps.get(idx, [])),
            "reverse_dependencies": sorted(reverse_deps.get(idx, [])),
        })
    return graph


def build_dependency_graph(cells: list[dict]) -> tuple[DependencyTracker, dict[int, list[int]], dict[int, list[int]]]:
    tracker = DependencyTracker()
    for cell in cells:
        if str(cell.get("type") or "code") != "code":
            continue
        try:
            idx = int(cell.get("index", 0))
        except Exception:
            continue
        tracker.add_cell(idx, str(cell.get("input") or ""))
    tracker.compute_graph()

    reverse_deps: dict[int, list[int]] = {idx: [] for idx in tracker.symbol_table.keys()}
    for idx, deps in tracker.dependencies.items():
        for d in deps:
            if d in reverse_deps:
                reverse_deps[d].append(idx)

    return tracker, tracker.dependencies, reverse_deps


def _cell_by_index(cells: list[dict], index: int) -> dict | None:
    for c in cells:
        try:
            if int(c.get("index", 0)) == int(index):
                return c
        except Exception:
            continue
    return None


def _cell_has_output(cell: dict) -> bool:
    return bool(str(cell.get("output") or "").strip())


def _format_cell_block(
    cell: dict,
    *,
    include_output: bool = False,
    max_input: int | None = None,
    max_output: int | None = None,
) -> str:
    try:
        idx = int(cell.get("index", 0))
    except Exception:
        idx = 0
    ctype = str(cell.get("type") or "code")
    inp = str(cell.get("input") or "")
    if max_input is not None and len(inp) > max_input:
        inp = inp[:max_input] + "\n... [input truncated]"
    lines = [f"### Cell [{idx}] ({ctype})"]
    if cell.get("execution_order") is not None:
        lines.append(f"execution_order: {cell.get('execution_order')}")
    if cell.get("execution_title"):
        lines.append(f"execution_title: {cell.get('execution_title')}")
    lines.append("```python")
    lines.append(inp or "# empty")
    lines.append("```")
    if include_output and str(ctype) == "code":
        out = str(cell.get("output") or "").strip()
        cap = int(max_output if max_output is not None else MAX_CELL_OUTPUT_CHARS)
        if out and len(out) > cap:
            out = out[:cap] + "\n... [output truncated]"
        if out:
            lines.append("output:")
            lines.append("```")
            lines.append(out)
            lines.append("```")
        else:
            lines.append("output: (none captured — re-run cell or wait for scrape)")
    return "\n".join(lines)


def _transitive_deps(tracker: DependencyTracker, idx: int) -> list[int]:
    seen: set[int] = set()
    stack = list(tracker.dependencies.get(idx, []))

    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(tracker.dependencies.get(n, []))
    return sorted(seen)


def _manifest_block(
    coverage: str,
    snapshot: str,
    *,
    kernel_scenario: str = "unknown",
    cell_index: int | None = None,
    listed_cells: list[int] | None = None,
) -> str:
    cells_str = ", ".join(str(c) for c in (listed_cells or [])) or "none"
    return "\n".join(
        [
            "CONTEXT_MANIFEST",
            f"coverage: {coverage}",
            f"snapshot: {snapshot}",
            f"kernel_scenario: {kernel_scenario}",
            "cell_indexing: 1-based cell numbers (first cell is 1)",
            f"target_cell: {cell_index if cell_index is not None else 'none'}",
            f"listed_cells: {cells_str}",
            "rules: Only cite cells listed in listed_cells or in sections below. "
            "If a cell is not listed, say you do not have it.",
        ]
    )


def _truncate_at_cell_boundaries(body: str, max_chars: int) -> str:
    if len(body) <= max_chars:
        return body
    parts = body.split("### Cell [")
    if len(parts) <= 1:
        return body[:max_chars] + "\n... [context truncated at cell boundary]"
    out = parts[0]
    for part in parts[1:]:
        chunk = "### Cell [" + part
        if len(out) + len(chunk) > max_chars:
            out += "\n... [remaining cells omitted — context budget]"
            break
        out += chunk
    return out


def _pack_dependency(
    cells: list[dict],
    tracker: DependencyTracker,
    deps: dict[int, list[int]],
    reverse_deps: dict[int, list[int]],
    cell_index: int | None,
    meta: dict,
) -> tuple[str, list[int]]:
    sections: list[str] = []
    listed: list[int] = []

    sections.append("## Dependency graph (all code cells)")
    for cell in cells:
        if str(cell.get("type") or "code") != "code":
            continue
        try:
            idx = int(cell.get("index", 0))
        except Exception:
            continue
        up = deps.get(idx, [])
        down = reverse_deps.get(idx, [])
        preview = str(cell.get("input") or "")[:100].replace("\n", " ")
        sections.append(
            f"- Cell [{idx}]: depends_on={up or '[]'} | used_by={down or '[]'} | preview={preview!r}"
        )
        listed.append(idx)

    if cell_index is not None:
        up_direct = deps.get(cell_index, [])
        up_all = _transitive_deps(tracker, cell_index)
        down = reverse_deps.get(cell_index, [])
        sections.append(f"\n## Target cell [{cell_index}]")
        target = _cell_by_index(cells, cell_index)
        if target:
            sections.append(
                _format_cell_block(target, max_input=800, include_output=True, max_output=MAX_CELL_OUTPUT_CHARS)
            )
        sections.append(f"direct_upstream: {up_direct}")
        sections.append(f"transitive_upstream: {up_all}")
        sections.append(f"downstream: {down}")

    body = "\n".join(sections)
    manifest = _manifest_block(
        "full" if cells else "none",
        meta.get("snapshot", "none"),
        kernel_scenario=meta.get("kernel_scenario", "unknown"),
        cell_index=cell_index,
        listed_cells=listed,
    )
    return manifest + "\n\n" + body, listed


def _pack_explain_error(
    cells: list[dict],
    tracker: DependencyTracker,
    deps: dict[int, list[int]],
    cell_index: int | None,
    meta: dict,
) -> tuple[str, list[int]]:
    listed: list[int] = []
    sections: list[str] = []

    if cell_index is None:
        body = "No target cell index in prompt. Ask the user for the failing cell number."
        manifest = _manifest_block("partial", meta.get("snapshot", "none"), kernel_scenario=meta.get("kernel_scenario", "unknown"))
        return manifest + "\n\n" + body, listed

    target = _cell_by_index(cells, cell_index)
    symbol_sites: list[str] = []
    if target:
        target_block = _format_cell_block(
            target, include_output=True, max_output=MAX_CELL_OUTPUT_CHARS
        )
        try:
            from .symbol_graph import SYMBOL_CONTEXT_ENABLED, pack_target_with_symbols
        except Exception:
            from symbol_graph import SYMBOL_CONTEXT_ENABLED, pack_target_with_symbols
        if SYMBOL_CONTEXT_ENABLED:
            body_sym, symbol_sites = pack_target_with_symbols(
                cells, cell_index, target_block, include_output=True
            )
            if symbol_sites:
                sections.append(body_sym)
                listed.append(cell_index)
                for key in symbol_sites:
                    try:
                        listed.append(int(key.split(":", 1)[0]))
                    except Exception:
                        pass
            else:
                sections.append(target_block)
                listed.append(cell_index)
        else:
            sections.append(target_block)
            listed.append(cell_index)

    if not symbol_sites:
        up_all = _transitive_deps(tracker, cell_index) if cell_index is not None else []
        if up_all:
            sections.append("\n## Upstream cells (may define symbols used in target)")
            for u in up_all[:6]:
                c = _cell_by_index(cells, u)
                if c:
                    sections.append(_format_cell_block(c, include_output=True, max_input=600))
                    listed.append(u)

    body = "\n".join(sections) or "Target cell not found in snapshot."
    coverage = "full" if target else "partial"
    manifest = _manifest_block(
        coverage,
        meta.get("snapshot", "none"),
        kernel_scenario=meta.get("kernel_scenario", "unknown"),
        cell_index=cell_index,
        listed_cells=sorted(set(listed)),
    )
    return manifest + "\n\n" + body, listed


def _pack_explain_code(
    cells: list[dict],
    tracker: DependencyTracker,
    deps: dict[int, list[int]],
    cell_index: int | None,
    meta: dict,
) -> tuple[str, list[int]]:
    listed: list[int] = []
    sections: list[str] = []

    if cell_index is None:
        manifest = _manifest_block("partial", meta.get("snapshot", "none"), kernel_scenario=meta.get("kernel_scenario", "unknown"))
        return manifest + "\n\nAsk which cell to explain (e.g. 'cell 3').", listed

    target = _cell_by_index(cells, cell_index)
    symbol_sites: list[str] = []
    if target:
        target_block = _format_cell_block(
            target, include_output=True, max_output=MAX_CELL_OUTPUT_CHARS
        )
        try:
            from .symbol_graph import SYMBOL_CONTEXT_ENABLED, pack_target_with_symbols
        except Exception:
            from symbol_graph import SYMBOL_CONTEXT_ENABLED, pack_target_with_symbols
        if SYMBOL_CONTEXT_ENABLED:
            body_sym, symbol_sites = pack_target_with_symbols(
                cells, cell_index, target_block, include_output=True
            )
            if symbol_sites:
                sections.append(body_sym)
                listed.append(cell_index)
            else:
                sections.append(target_block)
                listed.append(cell_index)
        else:
            sections.append(target_block)
            listed.append(cell_index)

    if not symbol_sites:
        up_direct = deps.get(cell_index, []) if cell_index is not None else []
        if up_direct:
            sections.append("\n## Upstream definitions (preview)")
            for u in up_direct[:6]:
                c = _cell_by_index(cells, u)
                if c:
                    sections.append(
                        _format_cell_block(
                            c,
                            max_input=400,
                            include_output=_cell_has_output(c),
                            max_output=800,
                        )
                    )
                    listed.append(u)

    body = "\n".join(sections) or "Target cell not found in snapshot."
    manifest = _manifest_block(
        "full" if target else "partial",
        meta.get("snapshot", "none"),
        kernel_scenario=meta.get("kernel_scenario", "unknown"),
        cell_index=cell_index,
        listed_cells=sorted(set(listed)),
    )
    return manifest + "\n\n" + body, listed


def _pack_code_review(cells: list[dict], meta: dict) -> tuple[str, list[int]]:
    listed: list[int] = []
    sections: list[str] = ["## All code cells (review scope)"]
    for cell in cells:
        if str(cell.get("type") or "code") != "code":
            continue
        try:
            idx = int(cell.get("index", 0))
        except Exception:
            continue
        sections.append(
            _format_cell_block(
                cell,
                max_input=500,
                include_output=True,
                max_output=1200,
            )
        )
        listed.append(idx)
    body = "\n".join(sections)
    manifest = _manifest_block(
        "full" if listed else "none",
        meta.get("snapshot", "none"),
        kernel_scenario=meta.get("kernel_scenario", "unknown"),
        listed_cells=listed,
    )
    return manifest + "\n\n" + body, listed


def _pack_ask(
    cells: list[dict],
    tracker: DependencyTracker,
    deps: dict[int, list[int]],
    reverse_deps: dict[int, list[int]],
    cell_index: int | None,
    meta: dict,
    prompt: str,
) -> tuple[str, list[int]]:
    try:
        from .prompt_engineering import classify_ask_intent
    except Exception:
        from prompt_engineering import classify_ask_intent

    intent = classify_ask_intent(prompt)
    if intent == "placement":
        # Do not anchor on a misleading "CELL 39" in the question; use full graph.
        return _pack_dependency(cells, tracker, deps, reverse_deps, None, meta)
    if intent == "error":
        return _pack_explain_error(cells, tracker, deps, cell_index, meta)
    if intent == "dependency":
        return _pack_dependency(cells, tracker, deps, reverse_deps, cell_index, meta)
    if intent == "review":
        return _pack_code_review(cells, meta)
    if intent == "explain" and cell_index is not None:
        return _pack_explain_code(cells, tracker, deps, cell_index, meta)
    return _pack_simple(cells, cell_index, meta)


def _pack_code(
    cells: list[dict],
    tracker: DependencyTracker,
    deps: dict[int, list[int]],
    reverse_deps: dict[int, list[int]],
    cell_index: int | None,
    meta: dict,
    prompt: str,
) -> tuple[str, list[int]]:
    listed: list[int] = []
    sections: list[str] = ["## Code mode context"]

    if cell_index is not None:
        up = deps.get(cell_index, [])
        down = reverse_deps.get(cell_index, [])
        if up:
            sections.append(f"direct_upstream: {up}")
        if down:
            sections.append(f"downstream: {down}")

        target = _cell_by_index(cells, cell_index)
        if target:
            target_block = _format_cell_block(
                target, include_output=True, max_output=MAX_CELL_OUTPUT_CHARS
            )
            try:
                from .symbol_graph import SYMBOL_CONTEXT_ENABLED, pack_target_with_symbols
            except Exception:
                from symbol_graph import SYMBOL_CONTEXT_ENABLED, pack_target_with_symbols
            if SYMBOL_CONTEXT_ENABLED:
                body_sym, symbol_sites = pack_target_with_symbols(
                    cells, cell_index, target_block, include_output=True
                )
                if symbol_sites:
                    sections.append(body_sym)
                    listed.append(cell_index)
                else:
                    sections.append(target_block)
                    listed.append(cell_index)
            else:
                sections.append(target_block)
                listed.append(cell_index)
            for u in up[:6]:
                c = _cell_by_index(cells, u)
                if c:
                    sections.append(
                        _format_cell_block(
                            c,
                            max_input=400,
                            include_output=_cell_has_output(c),
                            max_output=600,
                        )
                    )
                    listed.append(u)
        else:
            sections.append(f"Target cell [{cell_index}] not found in snapshot.")
    else:
        preview_body, preview_listed = _pack_simple(cells, None, meta)
        sections.append(preview_body)
        listed.extend(preview_listed)

    body = "\n".join(sections)
    coverage = "full" if listed else "partial"
    manifest = _manifest_block(
        coverage,
        meta.get("snapshot", "none"),
        kernel_scenario=meta.get("kernel_scenario", "unknown"),
        cell_index=cell_index,
        listed_cells=sorted(set(listed)),
    )
    return manifest + "\n\n" + body, sorted(set(listed))


def _pack_simple(cells: list[dict], cell_index: int | None, meta: dict) -> tuple[str, list[int]]:
    listed: list[int] = []
    sections: list[str] = []
    if cell_index is not None:
        c = _cell_by_index(cells, cell_index)
        if c:
            sections.append(
                _format_cell_block(
                    c,
                    max_input=800,
                    include_output=True,
                    max_output=MAX_CELL_OUTPUT_CHARS,
                )
            )
            listed.append(cell_index)
    elif cells:
        sections.append("## Notebook previews (referenced cells)")
        for cell in cells[:5]:
            if str(cell.get("type") or "code") != "code":
                continue
            try:
                idx = int(cell.get("index", 0))
            except Exception:
                continue
            sections.append(
                _format_cell_block(
                    cell,
                    max_input=200,
                    include_output=True,
                    max_output=800,
                )
            )
            listed.append(idx)
    body = "\n".join(sections)
    coverage = "partial" if listed else "none"
    manifest = _manifest_block(
        coverage,
        meta.get("snapshot", "none"),
        kernel_scenario=meta.get("kernel_scenario", "unknown"),
        cell_index=cell_index,
        listed_cells=listed,
    )
    return manifest + "\n\n" + (body or "No notebook cells in snapshot."), listed


def pack_context(
    *,
    mode: str,
    url: str,
    prompt: str,
    cell_index: int | None = None,
    dep_manager: Any = None,
    bot_state: dict | None = None,
) -> ContextPack:
    """Build mode-specific notebook context with manifest."""
    mode = normalize_mode(mode)
    if cell_index is None:
        cell_index = _extract_cell_number(prompt)

    if mode == "ask":
        try:
            from .prompt_engineering import classify_ask_intent
        except Exception:
            from prompt_engineering import classify_ask_intent
        if classify_ask_intent(prompt) == "placement":
            cell_index = None

    meta = get_freshness_metadata(url, bot_state)
    data, source = load_notebook_snapshot(url)
    cells = _cells_from_data(data)

    if not cells:
        manifest = _manifest_block(
            "none",
            "none",
            kernel_scenario=meta.get("kernel_scenario", "unknown"),
            cell_index=cell_index,
        )
        return ContextPack(
            text=manifest + "\n\nNo notebook snapshot available. Ask user to wait for scrape or refresh the page.",
            coverage="none",
            snapshot="none",
            cell_index=cell_index,
            kernel_scenario=meta.get("kernel_scenario", "unknown"),
            manifest={"coverage": "none", "snapshot": "none"},
        )

    tracker, deps, reverse_deps = build_dependency_graph(cells)

    if mode == "code":
        body, listed = _pack_code(cells, tracker, deps, reverse_deps, cell_index, {**meta, "snapshot": source}, prompt)
    else:
        body, listed = _pack_ask(
            cells, tracker, deps, reverse_deps, cell_index, {**meta, "snapshot": source}, prompt
        )

    if meta.get("editor_loading"):
        body += "\n\nNOTE: Kernel/editor is loading — execution metadata may be stale."

    body = _truncate_at_cell_boundaries(body, MAX_NOTEBOOK_CONTEXT_CHARS)
    coverage = "none"
    if "coverage: full" in body:
        coverage = "full"
    elif "coverage: partial" in body:
        coverage = "partial"
    elif listed:
        coverage = "partial" if len(listed) < len([c for c in cells if c.get("type") == "code"]) else "full"

    return ContextPack(
        text=body,
        coverage=coverage,
        snapshot=source,
        cell_index=cell_index,
        kernel_scenario=meta.get("kernel_scenario", "unknown"),
        manifest={
            "coverage": coverage,
            "snapshot": source,
            "listed_cells": listed,
            "kernel_scenario": meta.get("kernel_scenario"),
        },
    )


def get_cell_context_text(url: str, cell_num: int, bot_state: dict | None = None) -> str:
    """Compatibility shim for get_cell_context(cell_num)."""
    pack = pack_context(
        mode="ask",
        url=url,
        prompt=f"cell {cell_num} dependencies",
        cell_index=cell_num,
        bot_state=bot_state,
    )
    return pack.text


def cell_slice_from_snapshot(url: str, cell_index: int) -> str:
    data, source = load_notebook_snapshot(url)
    cells = _cells_from_data(data)
    cell = _cell_by_index(cells, cell_index)
    if not cell:
        return json.dumps({"ok": False, "error": f"Cell {cell_index} not in snapshot"})
    return json.dumps(
        {
            "ok": True,
            "cell_index": cell_index,
            "snapshot": source,
            "cell": {
                "index": cell.get("index"),
                "type": cell.get("type"),
                "input": cell.get("input"),
                "output": cell.get("output"),
                "execution_order": cell.get("execution_order"),
            },
        },
        ensure_ascii=False,
    )
