"""
Per-cell execution metadata (order / title / status) — gated off until stable integration.

Set KERNEL_EXECUTION_METADATA_ENABLED=1 in the environment to re-enable.
"""

from __future__ import annotations

from typing import Any

_EXECUTION_FIELD_NAMES = (
    "execution_order",
    "execution_title",
    "execution_status",
    "execution_signal",
    "execution_timestamp",
)


def enabled() -> bool:
    try:
        from .config import KERNEL_EXECUTION_METADATA_ENABLED
    except Exception:
        try:
            from config import KERNEL_EXECUTION_METADATA_ENABLED
        except Exception:
            from testing.host.config import KERNEL_EXECUTION_METADATA_ENABLED
    return bool(KERNEL_EXECUTION_METADATA_ENABLED)


def strip_cell(cell: dict[str, Any]) -> dict[str, Any]:
    out = dict(cell)
    for key in _EXECUTION_FIELD_NAMES:
        out.pop(key, None)
    return out


def strip_cells(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [strip_cell(c) for c in cells if isinstance(c, dict)]


def code_save_slice(cell: dict[str, Any]) -> dict[str, Any]:
    """Persisted code cell without execution metadata."""
    out: dict[str, Any] = {
        "type": "code",
        "index": cell.get("index"),
        "input": cell.get("input", ""),
        "output": cell.get("output", ""),
    }
    uuid = cell.get("uuid") or cell.get("data_uuid")
    if uuid:
        out["uuid"] = str(uuid)
    return out
