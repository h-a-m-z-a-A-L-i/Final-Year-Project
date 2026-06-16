"""Cell execution snapshot — detect runs from notebook state, not dispatch ack."""

from __future__ import annotations

import hashlib
import json
from typing import Any

try:
    from .execution_metadata import enabled as _execution_metadata_enabled
except Exception:
    try:
        from execution_metadata import enabled as _execution_metadata_enabled
    except Exception:
        from testing.host.execution_metadata import enabled as _execution_metadata_enabled
def _hash_text(value: str | None) -> str:
    text = str(value or "")
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def cell_execution_snapshot(cell: dict[str, Any] | None) -> dict[str, Any]:
    """Capture fields for before/after comparison."""
    c = dict(cell or {})
    source = str(c.get("input") or c.get("source") or "")
    output = str(c.get("output") or "")
    snap: dict[str, Any] = {
        "output_hash": _hash_text(output),
        "source_hash": _hash_text(source),
        "source_preview": source[:500],
        "output_preview": output[:800],
    }
    if _execution_metadata_enabled():
        snap.update({
            "execution_order": c.get("execution_order"),
            "execution_title": c.get("execution_title"),
            "metadata_ts": c.get("last_executed")
            or c.get("lastExecuted")
            or c.get("last_updated")
            or c.get("lastUpdated"),
        })
    return snap

def execution_order_increased(before: dict[str, Any], after: dict[str, Any]) -> bool:
    try:
        b = before.get("execution_order")
        a = after.get("execution_order")
        if b is None or a is None:
            return False
        return int(a) > int(b)
    except (TypeError, ValueError):
        return False


def snapshot_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if _execution_metadata_enabled():
        keys = ("execution_order", "execution_title", "output_hash", "source_hash", "metadata_ts")
        return any(before.get(k) != after.get(k) for k in keys)
    return before.get("output_hash") != after.get("output_hash") or before.get("source_hash") != after.get("source_hash")

def detect_run_verification(
    before_cell: dict[str, Any] | None,
    after_cell: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Run verified only when notebook state proves execution occurred.
    NOT when dispatch returned ok.
    """
    before = cell_execution_snapshot(before_cell)
    after = cell_execution_snapshot(after_cell)
    order_up = execution_order_increased(before, after) if _execution_metadata_enabled() else False
    changed = snapshot_changed(before, after)
    run_verified = bool(order_up or changed)
    return {
        "run_verified": run_verified,
        "execution_order_increased": order_up,
        "snapshot_changed": changed,
        "before": before,
        "after": after,
        "execution_order": after.get("execution_order"),
        "execution_title": after_cell.get("execution_title") if after_cell else None,
        "source": after.get("source_preview", ""),
        "output": after.get("output_preview", ""),
    }


def format_cell_run_evidence(evidence: dict[str, Any]) -> str:
    """Human-readable EXECUTION REPORT block for ReAct context."""
    ci = evidence.get("cell_index", "?")
    run_verified = evidence.get("run_verified")
    success = evidence.get("success")
    lines = [
        "EXECUTION REPORT",
        "",
        f"Cell {ci}",
        f"Run verified: {'YES' if run_verified else 'NO'}",
        f"Success: {'YES' if success else 'NO'}",
    ]
    if evidence.get("execution_order") is not None:
        lines.append(f"Execution order: {evidence['execution_order']}")
    if evidence.get("traceback"):
        lines.extend(["", "Traceback:", str(evidence["traceback"])])
    elif evidence.get("output"):
        lines.extend(["", "Output:", str(evidence["output"])[:2000]])
    elif evidence.get("source"):
        lines.extend(["", "Source:", str(evidence["source"])[:1500]])
    if evidence.get("wait_reason"):
        lines.append(f"Detection: {evidence['wait_reason']}")
    return "\n".join(lines)


def evidence_from_wait(wait: dict[str, Any]) -> dict[str, Any]:
    snap = wait.get("run_snapshot") or {}
    return {
        "cell_index": wait.get("cell_index"),
        "run_verified": bool(wait.get("run_verified")),
        "success": bool(wait.get("run_succeeded")) if wait.get("run_verified") else False,
        "execution_order": wait.get("execution_order"),
        "execution_title": wait.get("execution_title"),
        "source": snap.get("after", {}).get("source_preview") or wait.get("source"),
        "output": str(wait.get("output") or "")[:2000],
        "traceback": wait.get("error_summary") or wait.get("traceback"),
        "wait_reason": wait.get("wait_reason"),
        "before": snap.get("before"),
        "after": snap.get("after"),
    }


def evidence_to_json(evidence: dict[str, Any]) -> str:
    return json.dumps(evidence, ensure_ascii=False, indent=2)
