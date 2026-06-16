"""Strict agentic output rules — tools/code in batch format only."""

from __future__ import annotations

import re
from typing import Any

try:
    from .agentic_action_guard import is_actionable_notebook_request
except Exception:
    from agentic_action_guard import is_actionable_notebook_request

_BATCH_MARKER = re.compile(r"<agent_tool_batch\b", re.I)
_CODE_FENCE = re.compile(r"```(?:python|py|json)?\s*[\s\S]+?```", re.I)
_MANUAL_CODE = re.compile(
    r"(^\s*(?:import |from .+ import |def |class |df\[|pd\.|plt\.|print\())",
    re.I | re.M,
)


def has_tool_batch_marker(text: str) -> bool:
    return bool(_BATCH_MARKER.search(str(text or "")))


def contains_manual_code_without_tools(text: str) -> bool:
    """True when model pasted code but no <agent_tool_batch>."""
    body = str(text or "").strip()
    if not body or has_tool_batch_marker(body):
        return False
    if _CODE_FENCE.search(body):
        return True
    return bool(_MANUAL_CODE.search(body))


def strip_non_tool_prose(text: str) -> str:
    """Remove tool batch and fenced code — agentic assistant history should not carry manual code."""
    try:
        from .agentic_text_tools import strip_tool_batch_from_text
    except Exception:
        from agentic_text_tools import strip_tool_batch_from_text

    cleaned = strip_tool_batch_from_text(text)
    cleaned = _CODE_FENCE.sub("", cleaned).strip()
    return cleaned


def should_reject_prose_final(
    followup_text: str,
    *,
    prompt: str,
    verification: dict[str, Any] | None,
    tools_executed: int,
) -> bool:
    """
    Agentic actionable tasks must not end on prose unless execution was strictly verified.
    """
    if not is_actionable_notebook_request(prompt):
        return False

    if verification and verification.get("strict_goal_verified") is True:
        return False

    if verification and verification.get("continue_react_loop"):
        return True

    if verification and verification.get("strict_goal_verified") is False:
        return True

    if tools_executed == 0:
        return True

    if contains_manual_code_without_tools(followup_text):
        return True

    return False


def build_structured_output_nudge(*, prompt: str, reason: str, use_text_tools: bool = False) -> str:
    lines = [
        "AGENTIC MODE — structured output required.",
        f"Reason: {reason}",
        f"Task: {prompt.strip()}",
        "Reply with ONLY a valid tool batch (no prose, no manual code):",
    ]
    if use_text_tools:
        lines.append(
            '<agent_tool_batch>[{"tool":"edit_cell_by_index","args":{"cell_index":N,"content":"..."}},'
            '{"tool":"run_cell","args":{"cell_index":N}}]</agent_tool_batch>'
        )
    else:
        lines.append(
            "Use native API tool_calls only — emit every required function call in one response "
            "(insert_cell, edit_cell_by_index, run_cell). No markdown code blocks."
        )
    return "\n".join(lines)
