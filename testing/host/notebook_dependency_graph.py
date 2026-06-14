"""Notebook dependency graph — host-maintained symbol/cell dependency awareness."""

from __future__ import annotations

import ast
import json
import os
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT
except Exception:
    from config import DATA_ROOT

GRAPH_PATH = DATA_ROOT / "meta" / "notebook_dependency_graph.json"
_LOCK = threading.Lock()

_MAX_EDGES = 500
_MAX_SUMMARY_CHAINS = 4
_MAX_CHAIN_DEPTH = 6
_MAX_SUMMARY_CHARS = 900

_BUILTIN: set[str] = set()
try:
    _BUILTIN = set(dir(__builtins__))  # type: ignore[arg-type]
except Exception:
    pass

_NAME_ERR = re.compile(r"name ['\"](\w+)['\"]", re.I)


def dependency_graph_enabled() -> bool:
    raw = os.environ.get("AGENTIC_DEPENDENCY_GRAPH", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def empty_dependency_graph(*, notebook_key: str = "") -> dict[str, Any]:
    return {
        "notebook_key": notebook_key,
        "symbol_to_cell": {},
        "cell_to_symbols": {},
        "edges": [],
        "updated_at": None,
    }


def _is_user_name(name: str) -> bool:
    return bool(name) and name not in _BUILTIN and not name.startswith("_")


class _CellDepVisitor(ast.NodeVisitor):
    """Extract defines, uses, and intra-cell edges from one code cell."""

    def __init__(self, cell_index: int):
        self.cell_index = cell_index
        self.defines: set[str] = set()
        self.uses: set[str] = set()
        self.edges: list[dict[str, Any]] = []
        self._scope: str = "module"
        self._current_func: str | None = None

    def _add_edge(self, src: str, dst: str, kind: str) -> None:
        if not _is_user_name(src) or not _is_user_name(dst):
            return
        if src == dst:
            return
        self.edges.append(
            {
                "from": src,
                "to": dst,
                "kind": kind,
                "cell": self.cell_index,
            }
        )

    def _record_use(self, name: str) -> None:
        if _is_user_name(name):
            self.uses.add(name)
            if self._current_func:
                self._add_edge(name, self._current_func, "function->variable")

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._record_use(node.id)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name) and _is_user_name(t.id)]
        for t in targets:
            self.defines.add(t)
            self.edges.append(
                {"from": self.cell_index, "to": t, "kind": "cell->symbol", "cell": self.cell_index}
            )
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            fn = node.value.func.id
            if _is_user_name(fn):
                self._add_edge(fn, targets[0] if targets else fn, "function->variable")
                for arg in node.value.args:
                    if isinstance(arg, ast.Name):
                        self._add_edge(arg.id, fn, "function->variable")
        elif isinstance(node.value, ast.Name) and targets:
            self._add_edge(node.value.id, targets[0], "variable->variable")
        elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            if node.value.func.attr == "fit" and isinstance(node.value.func.value, ast.Name):
                model = node.value.func.value.id
                self.defines.add(model)
                for arg in node.value.args[:1]:
                    if isinstance(arg, ast.Name):
                        self._add_edge(arg.id, model, "model->dataset")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defines.add(node.name)
        self.edges.append(
            {"from": self.cell_index, "to": node.name, "kind": "cell->symbol", "cell": self.cell_index}
        )
        prev = self._current_func
        self._current_func = node.name
        self.generic_visit(node)
        self._current_func = prev

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defines.add(node.name)
        self.edges.append(
            {"from": self.cell_index, "to": node.name, "kind": "cell->symbol", "cell": self.cell_index}
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and _is_user_name(node.func.id):
            callee = node.func.id
            if self._current_func:
                self._add_edge(callee, self._current_func, "function->function")
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    self._add_edge(arg.id, callee, "function->variable")
        if isinstance(node.func, ast.Attribute) and node.func.attr == "fit":
            model = None
            if isinstance(node.func.value, ast.Name):
                model = node.func.value.id
            elif isinstance(node.func.value, ast.Attribute) and isinstance(node.func.value.value, ast.Name):
                model = node.func.value.value.id
            if model and _is_user_name(model):
                for arg in node.args[:1]:
                    if isinstance(arg, ast.Name):
                        self._add_edge(arg.id, model, "model->dataset")
        self.generic_visit(node)


def _parse_cell_dependencies(cell_index: int, code: str) -> dict[str, Any]:
    text = str(code or "")
    out: dict[str, Any] = {"defines": set(), "uses": set(), "edges": []}
    if not text.strip():
        return out
    try:
        tree = ast.parse(text)
    except SyntaxError:
        for m in re.finditer(r"^(\w+)\s*=", text, re.M):
            out["defines"].add(m.group(1))
            out["edges"].append(
                {"from": cell_index, "to": m.group(1), "kind": "cell->symbol", "cell": cell_index}
            )
        return out
    visitor = _CellDepVisitor(cell_index)
    visitor.visit(tree)
    out["defines"] = visitor.defines
    out["uses"] = visitor.uses
    out["edges"] = visitor.edges
    return out


def _normalize_edge(raw: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(raw.get("kind") or "")
    src = raw.get("from")
    dst = raw.get("to")
    cell = raw.get("cell")
    if kind == "cell->symbol":
        try:
            return {"from": int(src), "to": str(dst), "kind": kind, "cell": int(cell or src)}
        except (TypeError, ValueError):
            return None
    if not kind or src is None or dst is None:
        return None
    return {"from": str(src), "to": str(dst), "kind": kind, "cell": int(cell) if cell is not None else None}


def _merge_edges(existing: list[dict], new_edges: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for raw in list(existing) + list(new_edges):
        edge = _normalize_edge(raw) if isinstance(raw, dict) else None
        if not edge:
            continue
        key = (edge["kind"], str(edge["from"]), str(edge["to"]), edge.get("cell"))
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
        if len(out) >= _MAX_EDGES:
            break
    return out


def build_graph_from_notebook_data(data: dict[str, Any], *, notebook_key: str = "") -> dict[str, Any]:
    graph = empty_dependency_graph(notebook_key=notebook_key or str(data.get("tabUrl") or ""))
    symbol_to_cell: dict[str, int] = {}
    cell_to_symbols: dict[str, list[str]] = {}
    all_edges: list[dict] = []

    cells = sorted(
        (c for c in (data.get("cells") or []) if str(c.get("type") or "code") == "code"),
        key=lambda c: int(c.get("index") or 0),
    )

    for cell in cells:
        try:
            ci = int(cell.get("index"))
        except (TypeError, ValueError):
            continue
        code = str(cell.get("input") or "")
        parsed = _parse_cell_dependencies(ci, code)

        for sym in parsed["defines"]:
            symbol_to_cell[sym] = ci
        cell_to_symbols[str(ci)] = sorted(parsed["defines"], key=str.lower)

        for use in parsed["uses"]:
            owner = symbol_to_cell.get(use)
            if owner is not None and owner <= ci:
                all_edges.append(
                    {"from": use, "to": f"cell_{ci}", "kind": "variable->cell", "cell": ci}
                )

        for use in parsed["uses"]:
            for defined in parsed["defines"]:
                if use in symbol_to_cell:
                    all_edges.append(
                        {"from": use, "to": defined, "kind": "variable->variable", "cell": ci}
                    )

        all_edges.extend(parsed["edges"])

    graph["symbol_to_cell"] = symbol_to_cell
    graph["cell_to_symbols"] = cell_to_symbols
    graph["edges"] = _merge_edges([], all_edges)
    graph["updated_at"] = datetime.now(timezone.utc).isoformat()
    return graph


def build_graph_from_notebook_file(path: Path, *, notebook_key: str = "") -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return build_graph_from_notebook_data(data, notebook_key=notebook_key or str(data.get("tabUrl") or path.stem))


def update_graph_from_verification(
    graph: dict[str, Any] | None,
    verification: dict[str, Any],
    *,
    notebook_key: str = "",
) -> dict[str, Any]:
    out = deepcopy(graph) if isinstance(graph, dict) else empty_dependency_graph(notebook_key=notebook_key)
    if notebook_key:
        out["notebook_key"] = notebook_key

    symbol_to_cell = dict(out.get("symbol_to_cell") or {})
    cell_to_symbols = dict(out.get("cell_to_symbols") or {})
    new_edges: list[dict] = list(out.get("edges") or [])

    evidence = verification.get("queue_cell_evidence") or {}
    cells = evidence.get("cells") if isinstance(evidence, dict) else None
    sources: list[tuple[int, str]] = []
    if isinstance(cells, list):
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            try:
                ci = int(cell.get("cell_index"))
            except (TypeError, ValueError):
                continue
            code = str(cell.get("input") or cell.get("source") or cell.get("content") or "")
            if code.strip():
                sources.append((ci, code))

    edits = verification.get("expected_edits") or {}
    if isinstance(edits, dict):
        for ci_raw, content in edits.items():
            try:
                sources.append((int(ci_raw), str(content or "")))
            except (TypeError, ValueError):
                continue

    for ci, code in sources:
        parsed = _parse_cell_dependencies(ci, code)
        cell_to_symbols[str(ci)] = sorted(parsed["defines"], key=str.lower)
        for sym in parsed["defines"]:
            symbol_to_cell[sym] = ci
        new_edges.extend(parsed["edges"])
        for use in parsed["uses"]:
            owner = symbol_to_cell.get(use)
            if owner is not None:
                new_edges.append(
                    {"from": use, "to": f"cell_{ci}", "kind": "variable->cell", "cell": ci}
                )

    out["symbol_to_cell"] = symbol_to_cell
    out["cell_to_symbols"] = cell_to_symbols
    out["edges"] = _merge_edges(new_edges, [])
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    return out


def lookup_symbol_cell(graph: dict[str, Any] | None, symbol: str) -> int | None:
    if not isinstance(graph, dict) or not symbol:
        return None
    raw = (graph.get("symbol_to_cell") or {}).get(str(symbol).strip())
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _downstream_adjacency(graph: dict[str, Any]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        if edge.get("kind") == "cell->symbol":
            continue
        src = str(edge.get("from") or "")
        dst = str(edge.get("to") or "")
        if not src or not dst or dst.startswith("cell_"):
            continue
        adj.setdefault(src, [])
        if dst not in adj[src]:
            adj[src].append(dst)
    return adj


def analyze_impact(graph: dict[str, Any] | None, symbol: str) -> dict[str, Any]:
    """Return affected symbols, cells, and models when `symbol` changes."""
    if not isinstance(graph, dict) or not symbol:
        return {"symbol": symbol, "affected_symbols": [], "affected_cells": [], "affected_models": []}

    sym = str(symbol).strip()
    adj = _downstream_adjacency(graph)
    symbol_to_cell = graph.get("symbol_to_cell") or {}

    affected_symbols: list[str] = []
    queue = [sym]
    seen = {sym}
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            affected_symbols.append(nxt)
            queue.append(nxt)

    affected_cells: set[int] = set()
    owner = lookup_symbol_cell(graph, sym)
    if owner is not None:
        affected_cells.add(owner)
    for s in [sym] + affected_symbols:
        ci = lookup_symbol_cell(graph, s)
        if ci is not None:
            affected_cells.add(ci)

    models = set(graph.get("models") or [])
    for edge in graph.get("edges") or []:
        if edge.get("kind") == "model->dataset" and str(edge.get("from")) == sym:
            affected_symbols.append(str(edge.get("to")))
        if edge.get("kind") == "model->dataset" and str(edge.get("to")) in affected_symbols:
            pass

    affected_models = [
        s for s in affected_symbols
        if s.lower().startswith("model") or s in ("clf", "classifier")
        or any(k in s.lower() for k in ("model", "clf", "classifier"))
    ]
    for s in affected_symbols:
        if edge_targets_model(graph, s):
            if s not in affected_models:
                affected_models.append(s)

    return {
        "symbol": sym,
        "affected_symbols": affected_symbols[:20],
        "affected_cells": sorted(affected_cells),
        "affected_models": affected_models[:10],
    }


def edge_targets_model(graph: dict[str, Any], symbol: str) -> bool:
    for edge in graph.get("edges") or []:
        if edge.get("kind") == "model->dataset" and str(edge.get("to")) == symbol:
            return True
        if edge.get("kind") == "model->dataset" and str(edge.get("from")) == symbol:
            return True
    return str(symbol).lower().startswith("model") or symbol in ("clf",)


def _build_chains(graph: dict[str, Any], *, max_chains: int = _MAX_SUMMARY_CHAINS) -> list[list[str]]:
    """Find dependency chains from datasets/dataframes toward models."""
    adj = _downstream_adjacency(graph)
    symbol_to_cell = graph.get("symbol_to_cell") or {}
    seeds = [
        s for s in symbol_to_cell
        if re.search(r"(?:_df|_data|df$|train|test)", s, re.I) or s in ("X", "y")
    ]
    if not seeds:
        seeds = list(symbol_to_cell.keys())[:5]

    chains: list[list[str]] = []

    def dfs(path: list[str], depth: int) -> None:
        if len(chains) >= max_chains:
            return
        if depth >= _MAX_CHAIN_DEPTH:
            return
        cur = path[-1]
        nxts = adj.get(cur, [])
        if not nxts:
            if len(path) >= 2:
                chains.append(list(path))
            return
        for nxt in nxts[:4]:
            if nxt in path:
                continue
            dfs(path + [nxt], depth + 1)

    for seed in seeds[:8]:
        dfs([seed], 0)
        if len(chains) >= max_chains:
            break

    chains.sort(key=len, reverse=True)
    unique: list[list[str]] = []
    seen: set[str] = set()
    for chain in chains:
        key = "->".join(chain)
        if key not in seen:
            seen.add(key)
            unique.append(chain)
    return unique[:max_chains]


def format_dependency_summary(
    graph: dict[str, Any] | None,
    *,
    focus_symbol: str | None = None,
) -> str:
    if not isinstance(graph, dict) or not graph.get("symbol_to_cell"):
        return ""

    lines = ["DEPENDENCY SUMMARY"]
    if focus_symbol:
        impact = analyze_impact(graph, focus_symbol)
        lines.append(f"\nImpact of `{focus_symbol}`:")
        if impact["affected_symbols"]:
            chain = " -> ".join([focus_symbol] + impact["affected_symbols"][:5])
            lines.append(chain)
        if impact["affected_cells"]:
            lines.append(f"Cells: {impact['affected_cells'][:8]}")
        if impact["affected_models"]:
            lines.append(f"Models: {', '.join(impact['affected_models'][:5])}")

    chains = _build_chains(graph)
    for chain in chains:
        lines.append("\n" + chain[0])
        for step in chain[1:]:
            lines.append(f"-> {step}")

    block = "\n".join(lines)
    if len(block) > _MAX_SUMMARY_CHARS:
        block = block[: _MAX_SUMMARY_CHARS - 20] + "\n...[truncated]"
    return block


def build_smart_repair_hints(
    graph: dict[str, Any] | None,
    *,
    error_cell: int | None = None,
    error_summary: str = "",
    failed_symbol: str | None = None,
) -> dict[str, Any]:
    """Prioritize owner/dependency cells before broad notebook_get_cell scans."""
    if not isinstance(graph, dict):
        return {"priority_cells": [], "priority_symbols": [], "hint": ""}

    symbol = failed_symbol
    if not symbol and error_summary:
        m = _NAME_ERR.search(error_summary)
        if m:
            symbol = m.group(1)

    priority_cells: list[int] = []
    priority_symbols: list[str] = []

    if symbol:
        impact = analyze_impact(graph, symbol)
        owner = lookup_symbol_cell(graph, symbol)
        if owner is not None:
            priority_cells.append(owner)
        priority_symbols.extend(impact.get("affected_symbols") or [])
        priority_cells.extend(impact.get("affected_cells") or [])

    if error_cell is not None:
        try:
            ec = int(error_cell)
            if ec not in priority_cells:
                priority_cells.insert(0, ec)
            for sym, ci in (graph.get("symbol_to_cell") or {}).items():
                if int(ci) == ec and sym not in priority_symbols:
                    priority_symbols.append(sym)
        except (TypeError, ValueError):
            pass

    dedup_cells: list[int] = []
    seen_c: set[int] = set()
    for ci in priority_cells:
        if ci not in seen_c:
            seen_c.add(ci)
            dedup_cells.append(ci)

    hint = ""
    if dedup_cells or priority_symbols:
        parts = []
        if dedup_cells:
            parts.append(f"Inspect cells (priority order): {dedup_cells[:6]}")
        if priority_symbols:
            parts.append(f"Upstream symbols: {', '.join(priority_symbols[:8])}")
        parts.append("Use owner cells before notebook_get_cell scans.")
        hint = " ".join(parts)

    return {
        "priority_cells": dedup_cells[:8],
        "priority_symbols": priority_symbols[:12],
        "hint": hint,
    }


def estimate_dependency_tokens(graph: dict[str, Any] | None) -> int:
    return max(0, len(format_dependency_summary(graph)) // 4)


def _load_store() -> dict[str, Any]:
    if not GRAPH_PATH.is_file():
        return {}
    try:
        data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_store(data: dict[str, Any]) -> None:
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_dependency_graph(notebook_key: str) -> dict[str, Any] | None:
    key = str(notebook_key or "").strip()
    if not key:
        return None
    with _LOCK:
        raw = _load_store().get(key)
    if isinstance(raw, dict) and raw.get("symbol_to_cell"):
        return raw
    return None


def save_dependency_graph(notebook_key: str, graph: dict[str, Any]) -> None:
    key = str(notebook_key or "").strip()
    if not key or not isinstance(graph, dict):
        return
    payload = dict(graph)
    payload["notebook_key"] = key
    with _LOCK:
        store = _load_store()
        store[key] = payload
        _save_store(store)


def ensure_dependency_graph(notebook_key: str) -> dict[str, Any]:
    key = str(notebook_key or "").strip()
    existing = load_dependency_graph(key)
    if existing:
        return existing
    if not key:
        return empty_dependency_graph()
    try:
        from .notebook_context import load_notebook_snapshot
    except Exception:
        try:
            from notebook_context import load_notebook_snapshot
        except Exception:
            return empty_dependency_graph(notebook_key=key)
    try:
        data, _ = load_notebook_snapshot(key)
        if isinstance(data, dict) and data.get("cells"):
            g = build_graph_from_notebook_data(data, notebook_key=key)
            save_dependency_graph(key, g)
            return g
    except Exception:
        pass
    return empty_dependency_graph(notebook_key=key)


def sync_dependency_graph_to_agent_state(
    state: dict[str, Any] | None,
    verification: dict[str, Any] | None = None,
    *,
    notebook_key: str = "",
) -> dict[str, Any]:
    if not dependency_graph_enabled():
        return deepcopy(state) if isinstance(state, dict) else {}
    out = deepcopy(state) if isinstance(state, dict) else {}
    try:
        from .notebook_semantic_index import get_active_notebook_key
    except Exception:
        from notebook_semantic_index import get_active_notebook_key
    key = str(notebook_key or get_active_notebook_key() or out.get("notebook_key") or "").strip()
    graph = ensure_dependency_graph(key) if key else empty_dependency_graph()
    if verification:
        graph = update_graph_from_verification(graph, verification, notebook_key=key)
        if key:
            save_dependency_graph(key, graph)

    out["notebook_dependency"] = {
        "symbol_to_cell": dict(list((graph.get("symbol_to_cell") or {}).items())[:30]),
        "edge_count": len(graph.get("edges") or []),
    }
    out["_dependency_graph_full"] = graph

    err = out.get("last_error")
    if isinstance(err, dict) and err:
        hints = build_smart_repair_hints(
            graph,
            error_cell=err.get("cell_index"),
            error_summary=str(err.get("error_summary") or ""),
        )
        if hints.get("hint"):
            err = dict(err)
            err["dependency_repair"] = hints
            out["last_error"] = err

    focus = None
    if isinstance(out.get("last_error"), dict):
        impact_sym = None
        err = out["last_error"]
        dr = err.get("dependency_repair") or {}
        syms = dr.get("priority_symbols") or []
        if syms:
            focus = syms[0]
        elif err.get("cell_index") is not None:
            for sym, ci in (graph.get("symbol_to_cell") or {}).items():
                if ci == err.get("cell_index"):
                    focus = sym
                    break
    out["_dependency_summary"] = format_dependency_summary(graph, focus_symbol=focus)
    return out
