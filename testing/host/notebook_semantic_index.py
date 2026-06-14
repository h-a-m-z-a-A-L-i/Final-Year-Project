"""Notebook semantic index — host-maintained structured understanding of notebook cells."""

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

INDEX_PATH = DATA_ROOT / "meta" / "notebook_semantic_index.json"
_LOCK = threading.Lock()
_ACTIVE_KEY = threading.local()

_DF_SUFFIX = re.compile(r"(?:_df|_data|df)$", re.I)
_DF_READ = re.compile(
    r"^\s*(\w+)\s*=\s*(?:pd\.|pandas\.)(?:read_csv|read_parquet|read_excel|DataFrame)",
    re.M,
)
_FILE_PATH = re.compile(
    r"""['"]([^'"]*(?:/kaggle/input/|/kaggle/working/|\.csv|\.parquet|\.pth|\.h5|\.pkl|\.json|\.png|\.jpg)[^'"]*)['"]""",
    re.I,
)
_MODEL_CLASS = re.compile(
    r"\b(RandomForest\w*|XGB\w*|LGBM\w*|CatBoost\w*|LinearRegression|SVC|SVR|LogisticRegression|"
    r"GradientBoosting\w*|Sequential|nn\.Module|keras\.Model)\s*\(",
    re.I,
)
_MODEL_ASSIGN = re.compile(r"^\s*(model\w*)\s*=", re.M | re.I)
_IMPORT_TOP = re.compile(r"^(?:import|from)\s+([\w\.]+)", re.M)

_MAX_LIST = 14
_MAX_RECENT = 8

_BUILTIN = set()
try:
    _BUILTIN = set(dir(__builtins__))  # type: ignore[arg-type]
except Exception:
    pass


def semantic_index_enabled() -> bool:
    raw = os.environ.get("AGENTIC_SEMANTIC_INDEX", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def set_active_notebook_key(key: str) -> None:
    _ACTIVE_KEY.notebook_key = str(key or "").strip()


def get_active_notebook_key() -> str:
    return str(getattr(_ACTIVE_KEY, "notebook_key", "") or "").strip()


def empty_semantic_index(*, notebook_key: str = "") -> dict[str, Any]:
    return {
        "notebook_key": notebook_key,
        "imports": [],
        "variables": [],
        "functions": [],
        "classes": [],
        "models": [],
        "dataframes": [],
        "file_paths": [],
        "recent_changes": [],
        "cells": {},
        "updated_at": None,
    }


def _unique_sorted(items: list[str], limit: int = _MAX_LIST) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        s = str(raw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return sorted(out, key=str.lower)


def _import_module_name(node: ast.Import | ast.ImportFrom) -> list[str]:
    names: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            names.append(alias.name.split(".")[0])
            names.append(alias.asname or top)
    elif isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        if mod:
            names.append(mod.split(".")[0])
            names.extend(part for part in mod.split(".") if part)
        for alias in node.names:
            if alias.name != "*":
                names.append(alias.asname or alias.name.split(".")[0])
    return names


def parse_cell_semantics(cell_index: int, code: str) -> dict[str, Any]:
    """Extract semantic categories from one code cell."""
    text = str(code or "")
    out: dict[str, Any] = {
        "cell_index": cell_index,
        "imports": [],
        "variables": [],
        "functions": [],
        "classes": [],
        "models": [],
        "dataframes": [],
        "file_paths": list(_FILE_PATH.findall(text)),
    }
    if not text.strip():
        return out

    for m in _DF_READ.finditer(text):
        out["dataframes"].append(m.group(1))
    for m in _MODEL_ASSIGN.finditer(text):
        out["models"].append(m.group(1))
    for m in _MODEL_CLASS.finditer(text):
        out["models"].append(m.group(1))

    try:
        tree = ast.parse(text)
    except SyntaxError:
        for m in re.finditer(r"^(\w+)\s*=", text, re.M):
            name = m.group(1)
            if name not in _BUILTIN:
                if _DF_SUFFIX.search(name):
                    out["dataframes"].append(name)
                else:
                    out["variables"].append(name)
        out["imports"] = _IMPORT_TOP.findall(text)
        return out

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out["imports"].extend(_import_module_name(node))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out["functions"].append(f"{node.name}()")
        elif isinstance(node, ast.ClassDef):
            out["classes"].append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    if _DF_SUFFIX.search(name):
                        out["dataframes"].append(name)
                    elif name.lower().startswith("model"):
                        out["models"].append(name)
                    elif name not in _BUILTIN:
                        out["variables"].append(name)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            out["variables"].append(elt.id)

    if isinstance(tree.body, list):
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Attribute) and func.attr == "fit":
                    if isinstance(func.value, ast.Name):
                        out["models"].append(func.value.id)

    for key in ("imports", "variables", "functions", "classes", "models", "dataframes", "file_paths"):
        out[key] = _unique_sorted(out[key], limit=_MAX_LIST)
    return out


def _merge_cell_into_index(index: dict[str, Any], cell_sem: dict[str, Any]) -> None:
    ci = cell_sem.get("cell_index")
    if ci is None:
        return
    cells = index.setdefault("cells", {})
    cells[str(ci)] = cell_sem
    buckets = ("imports", "variables", "functions", "classes", "models", "dataframes", "file_paths")
    for bucket in buckets:
        merged: list[str] = list(index.get(bucket) or [])
        merged.extend(cell_sem.get(bucket) or [])
        index[bucket] = _unique_sorted(merged)


def _record_recent(index: dict[str, Any], cell_index: int, action: str) -> None:
    recent = list(index.get("recent_changes") or [])
    entry = {
        "cell_index": int(cell_index),
        "action": action,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    recent = [r for r in recent if not (r.get("cell_index") == entry["cell_index"] and r.get("action") == action)]
    recent.insert(0, entry)
    index["recent_changes"] = recent[:_MAX_RECENT]


def _rebuild_aggregates(index: dict[str, Any]) -> None:
    buckets = {k: [] for k in ("imports", "variables", "functions", "classes", "models", "dataframes", "file_paths")}
    for cell_sem in (index.get("cells") or {}).values():
        if not isinstance(cell_sem, dict):
            continue
        for bucket in buckets:
            buckets[bucket].extend(cell_sem.get(bucket) or [])
    for bucket, items in buckets.items():
        index[bucket] = _unique_sorted(items)


def update_cell_in_index(
    index: dict[str, Any],
    cell_index: int,
    code: str,
    *,
    action: str = "modified",
) -> dict[str, Any]:
    out = deepcopy(index) if isinstance(index, dict) else empty_semantic_index()
    sem = parse_cell_semantics(cell_index, code)
    out.setdefault("cells", {})[str(cell_index)] = sem
    _rebuild_aggregates(out)
    _record_recent(out, cell_index, action)
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    return out


def build_index_from_notebook_data(data: dict[str, Any], *, notebook_key: str = "") -> dict[str, Any]:
    out = empty_semantic_index(notebook_key=notebook_key)
    cells = data.get("cells") or []
    for cell in cells:
        if str(cell.get("type") or "code") != "code":
            continue
        try:
            ci = int(cell.get("index"))
        except (TypeError, ValueError):
            continue
        code = str(cell.get("input") or "")
        sem = parse_cell_semantics(ci, code)
        out.setdefault("cells", {})[str(ci)] = sem
        if cell.get("execution_order") is not None and str(cell.get("output") or "").strip():
            _record_recent(out, ci, "executed")
    _rebuild_aggregates(out)
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    return out


def build_index_from_notebook_file(path: Path, *, notebook_key: str = "") -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    key = notebook_key or str(data.get("tabUrl") or path.stem)
    return build_index_from_notebook_data(data, notebook_key=key)


def update_semantic_index_from_verification(
    index: dict[str, Any] | None,
    verification: dict[str, Any],
    *,
    notebook_key: str = "",
) -> dict[str, Any]:
    out = deepcopy(index) if isinstance(index, dict) else empty_semantic_index(notebook_key=notebook_key)
    if notebook_key:
        out["notebook_key"] = notebook_key

    evidence = verification.get("queue_cell_evidence") or {}
    cells = evidence.get("cells") if isinstance(evidence, dict) else None
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
                out = update_cell_in_index(out, ci, code, action="modified")
            if str(cell.get("output") or "").strip():
                _record_recent(out, ci, "executed")

    for item in verification.get("executed") or []:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        try:
            ci = int(item.get("cell_index"))
        except (TypeError, ValueError):
            continue
        if tool in ("edit_cell_by_index", "insert_cell", "edit_and_run_cell"):
            _record_recent(out, ci, "modified")
        elif tool == "run_cell":
            _record_recent(out, ci, "executed")

    edits = verification.get("expected_edits") or {}
    if isinstance(edits, dict):
        for ci_raw, content in edits.items():
            try:
                ci = int(ci_raw)
            except (TypeError, ValueError):
                continue
            out = update_cell_in_index(out, ci, str(content or ""), action="modified")

    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    return out


def format_semantic_index_block(index: dict[str, Any] | None) -> str:
    if not isinstance(index, dict):
        return ""
    if not any(
        index.get(k)
        for k in ("imports", "variables", "functions", "classes", "models", "dataframes", "file_paths", "recent_changes")
    ):
        return ""

    lines = ["NOTEBOOK STATE"]
    sections = (
        ("Imports", "imports", lambda x: x),
        ("DataFrames", "dataframes", lambda x: x),
        ("Functions", "functions", lambda x: x),
        ("Classes", "classes", lambda x: x),
        ("Models", "models", lambda x: x),
        ("Variables", "variables", lambda x: x),
        ("File paths", "file_paths", lambda x: x[:80]),
    )
    for title, key, fmt in sections:
        items = index.get(key) or []
        if items:
            lines.append(f"\n{title}:")
            for item in items[:12]:
                lines.append(f"- {fmt(item)}")

    recent = index.get("recent_changes") or []
    if recent:
        lines.append("\nRecent Changes:")
        for r in recent[:6]:
            ci = r.get("cell_index")
            action = r.get("action") or "changed"
            if action == "executed":
                lines.append(f"- Cell {ci} executed")
            else:
                lines.append(f"- Cell {ci} modified")
    return "\n".join(lines)


def estimate_index_tokens(index: dict[str, Any] | None) -> int:
    block = format_semantic_index_block(index)
    return max(0, len(block) // 4)


def lookup_symbol(index: dict[str, Any] | None, symbol: str) -> dict[str, Any] | None:
    """Return host index hit for a symbol name (reduces notebook_get_cell / find_symbol)."""
    if not isinstance(index, dict) or not symbol:
        return None
    sym = str(symbol).strip().rstrip("()")
    sym_lower = sym.lower()
    for bucket, kind in (
        ("dataframes", "dataframe"),
        ("functions", "function"),
        ("classes", "class"),
        ("models", "model"),
        ("variables", "variable"),
        ("imports", "import"),
    ):
        for item in index.get(bucket) or []:
            base = str(item).rstrip("()")
            if base.lower() == sym_lower or sym_lower in base.lower():
                cell_hits = []
                for ci, sem in (index.get("cells") or {}).items():
                    if not isinstance(sem, dict):
                        continue
                    if sym in (sem.get(bucket) or []) or base in (sem.get(bucket) or []):
                        cell_hits.append(int(ci))
                return {"symbol": sym, "kind": kind, "bucket": bucket, "cells": sorted(cell_hits)}
    return None


def _load_store() -> dict[str, Any]:
    if not INDEX_PATH.is_file():
        return {}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_store(data: dict[str, Any]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_semantic_index(notebook_key: str) -> dict[str, Any] | None:
    key = str(notebook_key or "").strip()
    if not key:
        return None
    with _LOCK:
        raw = _load_store().get(key)
    if isinstance(raw, dict) and raw.get("cells"):
        return raw
    return None


def save_semantic_index(notebook_key: str, index: dict[str, Any]) -> None:
    key = str(notebook_key or "").strip()
    if not key or not isinstance(index, dict):
        return
    index = dict(index)
    index["notebook_key"] = key
    with _LOCK:
        store = _load_store()
        store[key] = index
        _save_store(store)


def ensure_semantic_index(notebook_key: str) -> dict[str, Any]:
    """Load persisted index or bootstrap from notebook snapshot file."""
    key = str(notebook_key or "").strip()
    existing = load_semantic_index(key)
    if existing:
        return existing
    if not key:
        return empty_semantic_index()
    try:
        from .notebook_context import load_notebook_snapshot
    except Exception:
        try:
            from notebook_context import load_notebook_snapshot
        except Exception:
            return empty_semantic_index(notebook_key=key)
    try:
        data, _source = load_notebook_snapshot(key)
        if isinstance(data, dict) and data.get("cells"):
            idx = build_index_from_notebook_data(data, notebook_key=key)
            save_semantic_index(key, idx)
            return idx
    except Exception:
        pass
    return empty_semantic_index(notebook_key=key)


def sync_semantic_index_to_agent_state(
    state: dict[str, Any] | None,
    verification: dict[str, Any] | None = None,
    *,
    notebook_key: str = "",
) -> dict[str, Any]:
    """Update host semantic index and attach compact block to agent state."""
    if not semantic_index_enabled():
        return deepcopy(state) if isinstance(state, dict) else {}
    out = deepcopy(state) if isinstance(state, dict) else {}
    key = str(notebook_key or get_active_notebook_key() or out.get("notebook_key") or "").strip()
    if key:
        out["notebook_key"] = key
    idx = ensure_semantic_index(key) if key else empty_semantic_index()
    if verification:
        idx = update_semantic_index_from_verification(idx, verification, notebook_key=key)
        if key:
            save_semantic_index(key, idx)
    out["notebook_semantic"] = {
        "imports": idx.get("imports"),
        "dataframes": idx.get("dataframes"),
        "functions": idx.get("functions"),
        "classes": idx.get("classes"),
        "models": idx.get("models"),
        "variables": idx.get("variables"),
        "file_paths": idx.get("file_paths"),
        "recent_changes": idx.get("recent_changes"),
    }
    out["_semantic_index_full"] = idx
    return out
