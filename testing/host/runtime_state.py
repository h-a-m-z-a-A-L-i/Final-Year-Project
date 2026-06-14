"""Notebook runtime state — execution output awareness (shapes, metrics, files)."""

from __future__ import annotations

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

RUNTIME_PATH = DATA_ROOT / "meta" / "notebook_runtime_state.json"
_LOCK = threading.Lock()

_MAX_DF = 10
_MAX_MODELS = 8
MAX_RECENT_OUTPUTS = 50
_MAX_SUMMARY_CHARS = 780  # ~195 tokens

_SHAPE_PARENS = re.compile(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_SHAPE_KW = re.compile(r"shape\s*[\(:]?\s*(\d+)\s*,\s*(\d+)", re.I)
_METRIC = re.compile(
    r"\b(accuracy|acc|loss|precision|recall|f1|f1_score|auc|rmse|mae|r2|score)\b"
    r"\s*[=:]\s*(\d+\.?\d*)",
    re.I,
)
_METRIC_LINE = re.compile(
    r"\b(accuracy|acc|loss|precision|recall|f1|f1_score|auc|rmse|mae|r2)\b[^\d]{0,20}(\d+\.\d+|\d+)",
    re.I,
)
_FILE_OUT = re.compile(
    r"(?:saved|written|wrote|to)\s+['\"]?([^\s'\"]+(?:/kaggle/working/|\.pth|\.pkl|\.csv|\.json)[^\s'\"]*)",
    re.I,
)
_DF_VAR_SHAPE = re.compile(r"(\w+)\s*\.shape\s*[\(:]?\s*(\d+)\s*,\s*(\d+)", re.I)
_DF_PRINT_SHAPE = re.compile(r"(\w+)\.shape:\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)
_DTYPE_COUNT = re.compile(r"dtype:\s*(\d+)\s*object", re.I)


def runtime_state_enabled() -> bool:
    raw = os.environ.get("AGENTIC_RUNTIME_STATE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def empty_runtime_state(*, notebook_key: str = "") -> dict[str, Any]:
    return {
        "notebook_key": notebook_key,
        "dataframes": {},
        "models": {},
        "metrics": [],
        "variables": {},
        "file_outputs": [],
        "datasets": {},
        "recent_outputs": [],
        "updated_at": None,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_metric_value(raw: str) -> float | int:
    try:
        v = float(raw)
        return int(v) if v == int(v) and "." not in raw else v
    except (TypeError, ValueError):
        return 0


def extract_output_facts(
    cell_index: int,
    output: str,
    *,
    code: str = "",
) -> dict[str, Any]:
    """Parse executed cell output into compact runtime facts."""
    text = str(output or "")
    code_text = str(code or "")
    facts: dict[str, Any] = {
        "cell": cell_index,
        "dataframes": {},
        "models": {},
        "metrics": [],
        "file_outputs": [],
        "variables": {},
        "summary_parts": [],
    }
    if not text.strip() and not code_text.strip():
        return facts

    combined = f"{code_text}\n{text}"

    for m in _DF_VAR_SHAPE.finditer(combined):
        name, r, c = m.group(1), int(m.group(2)), int(m.group(3))
        facts["dataframes"][name] = {"shape": [r, c], "cell": cell_index}

    for m in _DF_PRINT_SHAPE.finditer(text):
        name, r, c = m.group(1), int(m.group(2)), int(m.group(3))
        facts["dataframes"][name] = {"shape": [r, c], "cell": cell_index}

    shapes = _SHAPE_PARENS.findall(text)
    if shapes and not facts["dataframes"]:
        r, c = int(shapes[0][0]), int(shapes[0][1])
        var = _guess_df_name(code_text) or f"cell_{cell_index}_df"
        facts["dataframes"][var] = {"shape": [r, c], "cell": cell_index}

    for m in _SHAPE_KW.finditer(text):
        r, c = int(m.group(1)), int(m.group(2))
        var = _guess_df_name(code_text) or f"cell_{cell_index}_df"
        facts["dataframes"].setdefault(var, {"shape": [r, c], "cell": cell_index})

    for m in _METRIC.finditer(text):
        name, val = m.group(1).lower(), _parse_metric_value(m.group(2))
        if name == "acc":
            name = "accuracy"
        facts["metrics"].append({"name": name, "value": val, "cell": cell_index})
        facts["summary_parts"].append(f"{name}={val}")

    for m in _METRIC_LINE.finditer(text):
        name, val = m.group(1).lower(), _parse_metric_value(m.group(2))
        if name == "acc":
            name = "accuracy"
        if not any(x["name"] == name for x in facts["metrics"]):
            facts["metrics"].append({"name": name, "value": val, "cell": cell_index})
            facts["summary_parts"].append(f"{name}={val}")

    model_name = _guess_model_name(code_text)
    acc_metrics = [x for x in facts["metrics"] if x["name"] in ("accuracy", "f1", "score", "auc")]
    if model_name and acc_metrics:
        best = acc_metrics[0]
        facts["models"][model_name] = {
            best["name"]: best["value"],
            "cell": cell_index,
        }
    elif acc_metrics:
        facts["models"][f"model_cell_{cell_index}"] = {
            acc_metrics[0]["name"]: acc_metrics[0]["value"],
            "cell": cell_index,
        }

    for m in _FILE_OUT.finditer(text):
        path = m.group(1).strip()
        facts["file_outputs"].append({"path": path[:120], "cell": cell_index})

    dc = _DTYPE_COUNT.search(text)
    if dc:
        facts["variables"]["columns_object"] = int(dc.group(1))

    cols_match = re.search(r"\[(\d+)\s+rows x (\d+)\s+columns\]", text, re.I)
    if cols_match:
        var = _guess_df_name(code_text) or f"cell_{cell_index}_df"
        facts["dataframes"].setdefault(
            var,
            {
                "shape": [int(cols_match.group(1)), int(cols_match.group(2))],
                "cell": cell_index,
            },
        )

    if facts["summary_parts"]:
        facts["output_summary"] = ", ".join(facts["summary_parts"][:4])
    elif shapes:
        facts["output_summary"] = f"shape=({shapes[0][0]},{shapes[0][1]})"
    elif text.strip():
        preview = " ".join(text.strip().split())[:80]
        facts["output_summary"] = preview

    return facts


def _guess_df_name(code: str) -> str | None:
    m = re.search(r"^\s*(\w+)\s*=\s*.*(?:read_csv|read_parquet|DataFrame|\.copy\(\))", code, re.M)
    if m:
        return m.group(1)
    m = re.search(r"^\s*(\w+)\s*=\s*\w+", code, re.M)
    if m and re.search(r"(?:_df|df$|_data)", m.group(1), re.I):
        return m.group(1)
    m = re.search(r"print\s*\(\s*(\w+)\.shape", code)
    if m:
        return m.group(1)
    return None


def _guess_model_name(code: str) -> str | None:
    m = re.search(r"^\s*(\w+)\s*=\s*.*(?:Classifier|Regressor|Model|fit\()", code, re.M)
    if m:
        return m.group(1)
    if re.search(r"\bmodel\.fit\b", code):
        return "model"
    if re.search(r"\bclf\s*=", code):
        return "clf"
    return None


def _merge_runtime(out: dict[str, Any], facts: dict[str, Any]) -> None:
    for name, info in (facts.get("dataframes") or {}).items():
        out["dataframes"][name] = {**info, "updated_at": _now()}
    for name, info in (facts.get("models") or {}).items():
        out["models"][name] = {**info, "updated_at": _now()}
    for metric in facts.get("metrics") or []:
        out["metrics"].append(metric)
        if len(out["metrics"]) > 50:
            out["metrics"] = out["metrics"][-50:]
    for fo in facts.get("file_outputs") or []:
        out["file_outputs"].append(fo)
        if len(out["file_outputs"]) > 20:
            out["file_outputs"] = out["file_outputs"][-20:]
    for k, v in (facts.get("variables") or {}).items():
        out["variables"][k] = v
    summary = facts.get("output_summary")
    if summary:
        recent = list(out.get("recent_outputs") or [])
        recent.insert(
            0,
            {
                "cell": facts.get("cell"),
                "summary": str(summary)[:120],
                "ts": _now(),
            },
        )
        out["recent_outputs"] = recent[:MAX_RECENT_OUTPUTS]


def build_runtime_from_notebook_data(data: dict[str, Any], *, notebook_key: str = "") -> dict[str, Any]:
    out = empty_runtime_state(notebook_key=notebook_key or str(data.get("tabUrl") or ""))
    for cell in data.get("cells") or []:
        if str(cell.get("type") or "code") != "code":
            continue
        try:
            ci = int(cell.get("index"))
        except (TypeError, ValueError):
            continue
        output = str(cell.get("output") or "")
        code = str(cell.get("input") or "")
        if output.strip():
            facts = extract_output_facts(ci, output, code=code)
            _merge_runtime(out, facts)
    out["updated_at"] = _now()
    return out


def build_runtime_from_notebook_file(path: Path, *, notebook_key: str = "") -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return build_runtime_from_notebook_data(data, notebook_key=notebook_key or str(data.get("tabUrl") or path.stem))


def update_runtime_from_verification(
    state: dict[str, Any] | None,
    verification: dict[str, Any],
    *,
    notebook_key: str = "",
) -> dict[str, Any]:
    out = deepcopy(state) if isinstance(state, dict) else empty_runtime_state(notebook_key=notebook_key)
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
            output = str(cell.get("output") or "")
            code = str(cell.get("input") or cell.get("source") or cell.get("content") or "")
            if output.strip():
                _merge_runtime(out, extract_output_facts(ci, output, code=code))

    err = verification.get("execution_error") or {}
    if not isinstance(err, dict):
        err = {"error_summary": str(err)} if err else {}
    err_out = str(err.get("output") or verification.get("cell_output") or "")
    err_ci = err.get("cell_index") or verification.get("cell_index")
    if err_out.strip() and err_ci is not None:
        try:
            _merge_runtime(out, extract_output_facts(int(err_ci), err_out))
        except (TypeError, ValueError):
            pass

    out["updated_at"] = _now()
    return out


def format_runtime_state_block(runtime: dict[str, Any] | None) -> str:
    if not isinstance(runtime, dict):
        return ""
    dfs = runtime.get("dataframes") or {}
    models = runtime.get("models") or {}
    recent = runtime.get("recent_outputs") or []
    if not dfs and not models and not recent:
        return ""

    lines = ["RUNTIME STATE"]
    if dfs:
        lines.append("\nDataFrames:")
        for name, info in list(dfs.items())[:_MAX_DF]:
            if not isinstance(info, dict):
                continue
            shape = info.get("shape")
            if shape and len(shape) >= 2:
                lines.append(f"- {name} ({shape[0]}x{shape[1]})")
            else:
                lines.append(f"- {name}")

    if models:
        lines.append("\nModels:")
        for name, info in list(models.items())[:_MAX_MODELS]:
            if not isinstance(info, dict):
                continue
            parts = []
            for k, v in info.items():
                if k in ("cell", "updated_at"):
                    continue
                parts.append(f"{k}={v}")
            label = ", ".join(parts) if parts else "trained"
            lines.append(f"- {name} {label}")

    if recent:
        lines.append("\nRecent Outputs:")
        for r in recent[:4]:
            ci = r.get("cell")
            sm = r.get("summary") or ""
            lines.append(f"- Cell {ci} {sm}"[:90])

    block = "\n".join(lines)
    if len(block) > _MAX_SUMMARY_CHARS:
        block = block[: _MAX_SUMMARY_CHARS - 18] + "\n...[truncated]"
    return block


def estimate_runtime_tokens(runtime: dict[str, Any] | None) -> int:
    return max(0, len(format_runtime_state_block(runtime)) // 4)


def build_error_runtime_context(
    runtime: dict[str, Any] | None,
    *,
    error_cell: int | None = None,
    related_symbols: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(runtime, dict):
        return {"relevant": [], "summary": ""}

    relevant: list[str] = []
    dfs = runtime.get("dataframes") or {}
    models = runtime.get("models") or {}

    for sym in related_symbols or []:
        if sym in dfs:
            info = dfs[sym]
            shape = info.get("shape")
            if shape:
                relevant.append(f"{sym} shape=({shape[0]},{shape[1]})")
        if sym in models:
            info = models[sym]
            metrics = [f"{k}={v}" for k, v in info.items() if k not in ("cell", "updated_at")]
            relevant.append(f"{sym} {', '.join(metrics)}")

    if error_cell is not None:
        for name, info in dfs.items():
            if isinstance(info, dict) and info.get("cell") == error_cell:
                shape = info.get("shape")
                if shape:
                    relevant.append(f"{name} shape=({shape[0]},{shape[1]})")
        for name, info in models.items():
            if isinstance(info, dict) and info.get("cell") == error_cell:
                metrics = [f"{k}={v}" for k, v in info.items() if k not in ("cell", "updated_at")]
                relevant.append(f"{name} {', '.join(metrics)}")

    recent = runtime.get("recent_outputs") or []
    for r in recent[:3]:
        if error_cell is None or r.get("cell") == error_cell:
            relevant.append(f"Cell {r.get('cell')} {r.get('summary')}")

    dedup: list[str] = []
    seen: set[str] = set()
    for item in relevant:
        if item not in seen:
            seen.add(item)
            dedup.append(item)

    summary = "; ".join(dedup[:6])
    return {"relevant": dedup[:8], "summary": summary}


def _load_store() -> dict[str, Any]:
    if not RUNTIME_PATH.is_file():
        return {}
    try:
        data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_store(data: dict[str, Any]) -> None:
    RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_runtime_state(notebook_key: str) -> dict[str, Any] | None:
    key = str(notebook_key or "").strip()
    if not key:
        return None
    with _LOCK:
        raw = _load_store().get(key)
    return raw if isinstance(raw, dict) else None


def save_runtime_state(notebook_key: str, runtime: dict[str, Any]) -> None:
    key = str(notebook_key or "").strip()
    if not key or not isinstance(runtime, dict):
        return
    payload = dict(runtime)
    payload["notebook_key"] = key
    with _LOCK:
        store = _load_store()
        store[key] = payload
        _save_store(store)


def ensure_runtime_state(notebook_key: str) -> dict[str, Any]:
    key = str(notebook_key or "").strip()
    existing = load_runtime_state(key)
    if existing and (existing.get("dataframes") or existing.get("models") or existing.get("recent_outputs")):
        return existing
    if not key:
        return empty_runtime_state()
    try:
        from .notebook_context import load_notebook_snapshot
    except Exception:
        try:
            from notebook_context import load_notebook_snapshot
        except Exception:
            return empty_runtime_state(notebook_key=key)
    try:
        data, _ = load_notebook_snapshot(key)
        if isinstance(data, dict) and data.get("cells"):
            rt = build_runtime_from_notebook_data(data, notebook_key=key)
            save_runtime_state(key, rt)
            return rt
    except Exception:
        pass
    return empty_runtime_state(notebook_key=key)


def sync_runtime_state_to_agent_state(
    state: dict[str, Any] | None,
    verification: dict[str, Any] | None = None,
    *,
    notebook_key: str = "",
) -> dict[str, Any]:
    if not runtime_state_enabled():
        return deepcopy(state) if isinstance(state, dict) else {}
    out = deepcopy(state) if isinstance(state, dict) else {}
    try:
        from .notebook_semantic_index import get_active_notebook_key
    except Exception:
        from notebook_semantic_index import get_active_notebook_key
    key = str(notebook_key or get_active_notebook_key() or out.get("notebook_key") or "").strip()
    runtime = ensure_runtime_state(key) if key else empty_runtime_state()
    if verification:
        runtime = update_runtime_from_verification(runtime, verification, notebook_key=key)
        if key:
            save_runtime_state(key, runtime)

    out["notebook_runtime"] = {
        "dataframe_count": len(runtime.get("dataframes") or {}),
        "model_count": len(runtime.get("models") or {}),
    }
    out["_runtime_state_full"] = runtime
    out["_runtime_summary"] = format_runtime_state_block(runtime)

    err = out.get("last_error")
    if isinstance(err, dict) and err:
        related: list[str] = []
        dr = err.get("dependency_repair") or {}
        related.extend(dr.get("priority_symbols") or [])
        ctx = build_error_runtime_context(
            runtime,
            error_cell=err.get("cell_index"),
            related_symbols=related,
        )
        if ctx.get("summary"):
            err = dict(err)
            err["runtime_context"] = ctx
            out["last_error"] = err

    return out
