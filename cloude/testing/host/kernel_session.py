"""Kernel session awareness — which cells actually ran since the last fresh kernel start."""

from __future__ import annotations

import re
from typing import Any

try:
    from .execution_metadata import enabled as _execution_metadata_enabled
except Exception:
    try:
        from execution_metadata import enabled as _execution_metadata_enabled
    except Exception:
        from testing.host.execution_metadata import enabled as _execution_metadata_enabled

_LOAD_CODE_HINT = re.compile(
    r"\b(read_csv|read_parquet|read_json|pd\.read_|load_dataset|/kaggle/input/)\b",
    re.IGNORECASE,
)

_SCENARIO_LABELS = {
    "scenario_1_new_notebook_off": "off",
    "scenario_2_kernel_on": "on",
    "scenario_2_fresh_kernel_started": "on",
    "scenario_3_reload_running_kernel": "on",
    "editor_loading": "loading",
    "unknown": "unknown",
}

_LIVE_BOT_STATE: dict[str, Any] = {}


def set_live_kernel_context(bot_state: dict | None) -> None:
    """Updated by host on each request with live extension kernel flags."""
    global _LIVE_BOT_STATE
    _LIVE_BOT_STATE = dict(bot_state or {})


def get_live_kernel_context() -> dict[str, Any]:
    return dict(_LIVE_BOT_STATE)


def _normalize_scenario(scenario: str) -> str:
    s = str(scenario or "").strip().lower()
    if s in _SCENARIO_LABELS:
        return s
    if "kernel_on" in s or "fresh" in s or "reload" in s:
        return "scenario_2_kernel_on"
    if "off" in s:
        return "scenario_1_new_notebook_off"
    return "unknown"


def _load_execution_notebook_state(url: str) -> dict[str, Any]:
    try:
        from .persistence_helpers import _load_execution_state
    except Exception:
        from persistence_helpers import _load_execution_state
    state = _load_execution_state()
    entry = state.get(url) if isinstance(state, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def resolve_kernel_scenario(url: str) -> dict[str, Any]:
    """Merge live bot_state with persisted execution_state kernel metadata."""
    live = get_live_kernel_context()
    persisted = _load_execution_notebook_state(url)

    scenario = _normalize_scenario(
        live.get("kernelScenario")
        or live.get("scenario")
        or persisted.get("last_kernel_scenario")
        or "unknown"
    )
    kernel_state = live.get("kernelState") if isinstance(live.get("kernelState"), dict) else {}
    return {
        "kernel_scenario": scenario,
        "kernel_scenario_label": _SCENARIO_LABELS.get(scenario, "unknown"),
        "kernel_status": live.get("kernelStatus"),
        "kernel_active": bool(persisted.get("kernel_active")),
        "kernel_session_started_at": persisted.get("kernel_session_started_at"),
        "kernel_session_stopped_at": persisted.get("kernel_session_stopped_at"),
        "editor_loading": bool(kernel_state.get("editorLoading")),
        "kernel_off_flag": bool(kernel_state.get("off")),
        "kernel_hdd_flag": bool(kernel_state.get("hdd")),
    }


def _revision_cell_flags(url: str) -> dict[int, dict[str, Any]]:
    persisted = _load_execution_notebook_state(url)
    rev_hash = persisted.get("active_revision")
    revisions = persisted.get("revisions") or {}
    rev = revisions.get(rev_hash, {}) if rev_hash else {}
    cells = rev.get("cells") if isinstance(rev, dict) else {}
    out: dict[int, dict[str, Any]] = {}
    if not isinstance(cells, dict):
        return out
    for key, val in cells.items():
        if not isinstance(val, dict):
            continue
        try:
            out[int(key)] = val
        except (TypeError, ValueError):
            continue
    return out


def classify_cell_session_status(
    cell: dict,
    *,
    kernel_scenario: str,
    revision_flags: dict[int, dict[str, Any]] | None = None,
) -> str:
    """
    Classify whether a code cell's output/order reflects the current kernel session.

    - ran_this_session: execution_order set (cell ran since metadata was reset/updated)
    - stale_output: visible output but no execution_order after fresh/off kernel
    - possibly_stale: reload scenario — output may not match in-memory kernel
    - not_executed: no output and no execution order
    """
    if str(cell.get("type") or "code") != "code":
        return "not_code"
    order = cell.get("execution_order")
    output = str(cell.get("output") or "").strip()
    has_output = bool(output)
    try:
        ci = int(cell.get("index"))
    except (TypeError, ValueError):
        ci = None
    flags = (revision_flags or {}).get(ci, {}) if ci is not None else {}

    if kernel_scenario in {"scenario_2_kernel_on", "scenario_2_fresh_kernel_started"}:
        if order is not None and flags.get("seen_running"):
            return "ran_this_session"
        if has_output:
            return "stale_output"
        return "not_executed"

    if order is not None:
        return "ran_this_session"
    if not has_output:
        return "not_executed"
    if kernel_scenario in {
        "scenario_2_kernel_on",
        "scenario_2_fresh_kernel_started",
        "scenario_1_new_notebook_off",
    }:
        return "stale_output"
    if kernel_scenario == "scenario_3_reload_running_kernel":
        return "possibly_stale"
    return "stale_output" if has_output else "not_executed"


def _is_data_load_cell(cell: dict) -> bool:
    inp = str(cell.get("input") or "")
    return bool(_LOAD_CODE_HINT.search(inp))


def _cell_summary(cell: dict, status: str) -> dict[str, Any]:
    inp = str(cell.get("input") or "")
    out = str(cell.get("output") or "")
    return {
        "index": int(cell.get("index")),
        "session_status": status,
        "execution_order": cell.get("execution_order"),
        "execution_title": str(cell.get("execution_title") or "").strip() or None,
        "has_output": bool(out.strip()),
        "output_preview": out[:200] if out else "",
        "input_preview": inp[:160].replace("\n", " "),
        "is_data_load": _is_data_load_cell(cell),
    }


def analyze_kernel_session(
    url: str,
    cells: list[dict],
    *,
    target_cell_index: int | None = None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Build kernel + per-cell session report for tools and prompts."""
    if not _execution_metadata_enabled():
        return {
            "ok": True,
            "disabled": True,
            "url": url,
            "kernel_scenario": "unknown",
            "kernel_scenario_label": "unknown",
            "summary": "Kernel execution metadata is disabled.",
            "guidance": "Re-run upstream cells before using variables from prior output.",
            "ran_this_session": [],
            "stale_output": [],
            "not_executed": [],
        }
    meta = resolve_kernel_scenario(url)
    scenario = meta["kernel_scenario"]
    revision_flags = _revision_cell_flags(url)

    ran: list[dict] = []
    stale: list[dict] = []
    possibly_stale: list[dict] = []
    not_executed: list[dict] = []
    stale_data_loads: list[dict] = []

    for cell in sorted(cells, key=lambda c: int(c.get("index", 0))):
        if str(cell.get("type") or "code") != "code":
            continue
        status = classify_cell_session_status(
            cell,
            kernel_scenario=scenario,
            revision_flags=revision_flags,
        )
        row = _cell_summary(cell, status)
        if status == "ran_this_session":
            ran.append(row)
        elif status == "stale_output":
            stale.append(row)
            if row.get("is_data_load"):
                stale_data_loads.append(row)
        elif status == "possibly_stale":
            possibly_stale.append(row)
        else:
            not_executed.append(row)

    prerequisite_runs: list[int] = []
    if target_cell_index is not None:
        prerequisite_runs = suggest_prerequisite_runs(
            cells,
            target_cell_index=int(target_cell_index),
            kernel_scenario=scenario,
            revision_flags=revision_flags,
        )

    symbol_hits: list[dict] = []
    if symbols:
        try:
            from .symbol_graph import build_symbol_index
        except Exception:
            from symbol_graph import build_symbol_index
        index = build_symbol_index(cells)
        for sym in symbols:
            sites = index.defs.get(str(sym)) or []
            for site in sites[:3]:
                ci = int(site.cell_index)
                cell = next((c for c in cells if int(c.get("index", -1)) == ci), None)
                if not cell:
                    continue
                st = classify_cell_session_status(
                    cell,
                    kernel_scenario=scenario,
                    revision_flags=revision_flags,
                )
                symbol_hits.append(
                    {
                        "symbol": sym,
                        "defined_in_cell": ci,
                        "session_status": st,
                        "needs_run_before_use": st != "ran_this_session",
                    }
                )

    guidance_parts: list[str] = []
    if scenario == "scenario_2_fresh_kernel_started":
        guidance_parts.append(
            "Fresh kernel: in-memory variables are empty unless a cell has execution_order "
            "(ran this session). Cells with old output but execution_order=null have STALE output only."
        )
    elif scenario == "scenario_1_new_notebook_off":
        guidance_parts.append("Kernel is off — run prerequisite cells before expecting variables like df.")
    elif scenario == "scenario_3_reload_running_kernel":
        guidance_parts.append(
            "Page reload: kernel may still hold variables, but snapshot output can be stale — "
            "prefer execution_order and notebook_kernel_state before assuming df exists."
        )
    if stale_data_loads:
        indices = [c["index"] for c in stale_data_loads]
        guidance_parts.append(
            f"Data-load cells with stale output (run before analysis): {indices}"
        )
    guidance_parts.append(
        "Do not delete or rewrite existing workflow cells unless the user asked. "
        "Reuse their variables (e.g. df) after re-running upstream cells, or create new names."
    )

    summary = (
        f"Kernel {meta['kernel_scenario_label']}: "
        f"{len(ran)} ran this session, {len(stale)} stale output, "
        f"{len(not_executed)} not executed"
    )
    if stale_data_loads:
        summary += f"; stale data-load cells: {[c['index'] for c in stale_data_loads]}"

    return {
        **meta,
        "url": url,
        "summary": summary,
        "guidance": " ".join(guidance_parts),
        "counts": {
            "ran_this_session": len(ran),
            "stale_output": len(stale),
            "possibly_stale": len(possibly_stale),
            "not_executed": len(not_executed),
            "stale_data_loads": len(stale_data_loads),
        },
        "ran_this_session": ran,
        "stale_output_cells": stale,
        "possibly_stale_cells": possibly_stale,
        "not_executed_cells": not_executed[:20],
        "stale_data_load_cells": stale_data_loads,
        "suggested_prerequisite_runs": prerequisite_runs,
        "symbol_session_hits": symbol_hits,
        "empty_output_likely_missing_upstream": bool(
            stale_data_loads and scenario == "scenario_2_fresh_kernel_started"
        ),
    }


def suggest_prerequisite_runs(
    cells: list[dict],
    *,
    target_cell_index: int,
    kernel_scenario: str,
    revision_flags: dict[int, dict[str, Any]] | None = None,
) -> list[int]:
    """
    Upstream cells that must run before target when they are not ran_this_session.
    Uses dependency graph + data-load heuristics.
    """
    try:
        from .notebook_context import build_dependency_graph
    except Exception:
        from notebook_context import build_dependency_graph

    _, deps, _ = build_dependency_graph(cells)
    upstream = list(deps.get(int(target_cell_index), []))
    ordered: list[int] = []

    for ci in sorted(set(upstream)):
        cell = next((c for c in cells if int(c.get("index", -1)) == int(ci)), None)
        if not cell:
            continue
        status = classify_cell_session_status(
            cell,
            kernel_scenario=kernel_scenario,
            revision_flags=revision_flags,
        )
        if status != "ran_this_session":
            ordered.append(int(ci))

    for cell in sorted(cells, key=lambda c: int(c.get("index", 0))):
        if not _is_data_load_cell(cell):
            continue
        ci = int(cell.get("index"))
        if ci >= int(target_cell_index):
            continue
        status = classify_cell_session_status(
            cell,
            kernel_scenario=kernel_scenario,
            revision_flags=revision_flags,
        )
        if status != "ran_this_session" and ci not in ordered:
            ordered.append(ci)

    return sorted(ordered)


def compact_kernel_session_for_prompt(report: dict[str, Any]) -> str:
    if not _execution_metadata_enabled() or report.get("disabled"):
        return ""
    """Short block for agent_state / verification nudges."""
    if not isinstance(report, dict):
        return ""
    lines = [
        "## Kernel session",
        report.get("summary") or "",
        str(report.get("guidance") or "").strip(),
    ]
    prereq = report.get("suggested_prerequisite_runs") or []
    if prereq:
        lines.append(f"suggested_prerequisite_runs: {prereq}")
    stale_loads = report.get("stale_data_load_cells") or []
    if stale_loads:
        lines.append(
            "stale_data_load_cells: "
            + ", ".join(f"{c['index']}" for c in stale_loads[:8])
        )
    sym = report.get("symbol_session_hits") or []
    needs = [s for s in sym if s.get("needs_run_before_use")]
    if needs:
        lines.append(
            "symbols_needing_upstream_run: "
            + ", ".join(f"{s['symbol']}@cell{s['defined_in_cell']}" for s in needs[:6])
        )
    return "\n".join(x for x in lines if x).strip()


def sync_kernel_session_to_agent_state(
    state: dict[str, Any] | None,
    verification: dict[str, Any] | None,
    *,
    notebook_key: str = "",
) -> dict[str, Any]:
    out = dict(state or {})
    if not _execution_metadata_enabled():
        return out
    ks = (verification or {}).get("kernel_session")
    if isinstance(ks, dict) and ks:
        out["kernel_session"] = ks
        return out
    url = str(notebook_key or out.get("notebook_key") or "").strip()
    if not url:
        return out
    try:
        from .notebook_context import load_notebook_snapshot, _cells_from_data
    except Exception:
        from notebook_context import load_notebook_snapshot, _cells_from_data
    data, _ = load_notebook_snapshot(url)
    cells = _cells_from_data(data)
    if not cells:
        return out
    report = analyze_kernel_session(url, cells)
    out["kernel_session"] = {
        "summary": report.get("summary"),
        "kernel_scenario_label": report.get("kernel_scenario_label"),
        "stale_data_load_cells": [c["index"] for c in report.get("stale_data_load_cells") or []],
        "suggested_prerequisite_runs": report.get("suggested_prerequisite_runs"),
        "guidance": report.get("guidance"),
    }
    return out
