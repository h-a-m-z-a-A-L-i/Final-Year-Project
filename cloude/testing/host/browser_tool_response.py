"""Standard success/error envelopes for isolated browser tools."""

from __future__ import annotations

from typing import Any


def validation_error(tool: str, message: str) -> dict[str, Any]:
    return {"ok": False, "tool": tool, "error": message}


def tool_success(tool: str, **fields: Any) -> dict[str, Any]:
    return {"ok": True, "tool": tool, **fields}


def tool_failure(
    tool: str,
    error: str,
    *,
    cmd: dict | None = None,
    event: dict | None = None,
    **fields: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "tool": tool, "error": error, **fields}
    if cmd is not None:
        if "cell_index" not in out and cmd.get("app_index") is not None:
            out["cell_index"] = cmd.get("app_index")
        if "dom_index" not in out and cmd.get("dom_index") is not None:
            out["dom_index"] = cmd.get("dom_index")
    if event is not None:
        out["details"] = event
    return out
