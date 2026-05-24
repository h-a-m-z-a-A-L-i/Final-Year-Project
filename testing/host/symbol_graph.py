"""Symbol-level provenance: index definitions per name and pack minimal snippets for context."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Iterable

try:
    from .config import MAX_SYMBOL_DEPTH, MAX_SYMBOL_SNIPPET_CHARS, SYMBOL_CONTEXT_ENABLED
except Exception:
    from config import MAX_SYMBOL_DEPTH, MAX_SYMBOL_SNIPPET_CHARS, SYMBOL_CONTEXT_ENABLED


@dataclass
class DefSite:
    cell_index: int
    name: str
    kind: str  # assign | function | class | import
    start_line: int
    end_line: int
    snippet: str


@dataclass
class SymbolIndex:
    defs: dict[str, list[DefSite]] = field(default_factory=dict)
    cell_uses: dict[int, set[str]] = field(default_factory=dict)
    cell_sources: dict[int, str] = field(default_factory=dict)


def _builtin_names() -> set[str]:
    try:
        return set(dir(__builtins__))  # type: ignore[arg-type]
    except Exception:
        return set()


_BUILTINS = _builtin_names()


def _lines_of(code: str) -> list[str]:
    return (code or "").splitlines()


def _snippet_from_lines(lines: list[str], start: int, end: int, max_chars: int) -> tuple[str, int, int]:
    if not lines:
        return "", 1, 1
    start = max(1, start)
    end = min(len(lines), max(start, end))
    chunk = "\n".join(lines[start - 1 : end])
    if len(chunk) > max_chars:
        chunk = chunk[:max_chars] + "\n... [truncated]"
    return chunk, start, end


def _parse_def_sites(cell_index: int, code: str, max_snippet: int) -> tuple[list[DefSite], set[str]]:
    sites: list[DefSite] = []
    uses: set[str] = set()
    lines = _lines_of(code)
    if not code.strip():
        return sites, uses

    try:
        tree = ast.parse(code)
    except SyntaxError:
        for m in re.finditer(r"^(\w+)\s*=", code, re.M):
            name = m.group(1)
            line_no = code[: m.start()].count("\n") + 1
            snip, s, e = _snippet_from_lines(lines, line_no, line_no, max_snippet)
            sites.append(DefSite(cell_index, name, "assign", s, e, snip))
        for m in re.finditer(r"\b([A-Za-z_]\w*)\b", code):
            if m.group(1) not in _BUILTINS:
                uses.add(m.group(1))
        return sites, uses

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in _BUILTINS:
                uses.add(node.id)

    for node in tree.body:
        start = getattr(node, "lineno", 1) or 1
        end = getattr(node, "end_lineno", None) or start

        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    snip, s, e = _snippet_from_lines(lines, start, end, max_snippet)
                    sites.append(DefSite(cell_index, t.id, "assign", s, e, snip))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            snip, s, e = _snippet_from_lines(lines, start, end, max_snippet)
            sites.append(DefSite(cell_index, node.name, "function", s, e, snip))
        elif isinstance(node, ast.ClassDef):
            snip, s, e = _snippet_from_lines(lines, start, end, max_snippet)
            sites.append(DefSite(cell_index, node.name, "class", s, e, snip))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                snip, s, e = _snippet_from_lines(lines, start, end, max_snippet)
                sites.append(DefSite(cell_index, name, "import", s, e, snip))

    return sites, uses


def build_symbol_index(cells: list[dict], max_snippet: int | None = None) -> SymbolIndex:
    cap = int(max_snippet or MAX_SYMBOL_SNIPPET_CHARS)
    index = SymbolIndex()
    ordered = sorted(
        (c for c in cells if str(c.get("type") or "code") == "code"),
        key=lambda c: int(c.get("index", 0)),
    )
    for cell in ordered:
        try:
            idx = int(cell.get("index", 0))
        except Exception:
            continue
        code = str(cell.get("input") or "")
        index.cell_sources[idx] = code
        sites, uses = _parse_def_sites(idx, code, cap)
        index.cell_uses[idx] = uses
        for site in sites:
            index.defs.setdefault(site.name, []).append(site)
    return index


def _latest_def_before(index: SymbolIndex, name: str, before_cell: int) -> DefSite | None:
    candidates = [d for d in index.defs.get(name, []) if d.cell_index < before_cell]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.cell_index)


def resolve_symbols_for_cell(
    index: SymbolIndex,
    target_cell: int,
    *,
    max_depth: int | None = None,
) -> dict[str, DefSite]:
    depth_limit = int(max_depth if max_depth is not None else MAX_SYMBOL_DEPTH)
    if target_cell not in index.cell_uses:
        return {}

    resolved: dict[str, DefSite] = {}
    queue: list[tuple[str, int]] = [(n, 0) for n in sorted(index.cell_uses.get(target_cell, set()))]
    seen_names: set[str] = set()

    while queue:
        name, depth = queue.pop(0)
        if name in seen_names:
            continue
        seen_names.add(name)
        site = _latest_def_before(index, name, target_cell)
        if site is None:
            continue
        resolved[name] = site
        if depth >= depth_limit:
            continue
        for nested in index.cell_uses.get(site.cell_index, set()):
            if nested not in seen_names:
                queue.append((nested, depth + 1))

    return resolved


def format_symbol_context(
    index: SymbolIndex,
    target_cell: int,
    target_block: str,
    *,
    include_output: bool = False,
) -> tuple[str, list[str]]:
    """Return (body_text, listed_site_keys like '2:df')."""
    if not SYMBOL_CONTEXT_ENABLED:
        return "", []

    symbols = resolve_symbols_for_cell(index, target_cell)
    sections: list[str] = []
    listed: list[str] = []

    if symbols:
        sections.append("## Symbol provenance (definitions used by target cell)")
        for name in sorted(symbols.keys()):
            site = symbols[name]
            key = f"{site.cell_index}:{name}"
            listed.append(key)
            sections.append(
                f"\n### Symbol `{name}` — defined in Cell [{site.cell_index}] "
                f"({site.kind}, lines {site.start_line}-{site.end_line})\n"
                f"```python\n{site.snippet}\n```"
            )

    sections.append(f"\n## Target Cell [{target_cell}]")
    sections.append(target_block)

    return "\n".join(sections), listed


def pack_target_with_symbols(
    cells: list[dict],
    cell_index: int,
    target_block: str,
    *,
    include_output: bool = False,
) -> tuple[str, list[str]]:
    index = build_symbol_index(cells)
    body, listed = format_symbol_context(
        index, cell_index, target_block, include_output=include_output
    )
    if body:
        return body, listed
    return target_block, listed
