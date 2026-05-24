"""Token budgeting for stateless API calls: trim history and fit messages to model limits."""

from __future__ import annotations

from typing import Any

try:
    from .config import (
        MAX_HISTORY_CHARS_PER_MSG,
        MAX_HISTORY_MESSAGES_API,
        MAX_INPUT_TOKENS,
        CHARS_PER_TOKEN_ESTIMATE,
    )
except Exception:
    from config import (
        MAX_HISTORY_CHARS_PER_MSG,
        MAX_HISTORY_MESSAGES_API,
        MAX_INPUT_TOKENS,
        CHARS_PER_TOKEN_ESTIMATE,
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(str(text)) // max(1, CHARS_PER_TOKEN_ESTIMATE))


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif content is not None:
            total += estimate_tokens(str(content))
        total += 4
    return total


def truncate_content(text: str, max_chars: int, *, suffix: str = "\n...[truncated for context budget]") -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    keep = max(0, max_chars - len(suffix))
    return s[:keep] + suffix


def trim_history_for_api(history: list[dict] | None) -> list[dict]:
    """Shrink stored history for inclusion in the next API request (UI keeps full text in DB)."""
    if not history:
        return []
    recent = list(history)[-MAX_HISTORY_MESSAGES_API:]
    trimmed: list[dict] = []
    for item in recent:
        role = str(item.get("role", "")).strip().lower()
        if role not in {"user", "assistant", "system"}:
            continue
        content = truncate_content(str(item.get("content", "")), MAX_HISTORY_CHARS_PER_MSG)
        if content:
            trimmed.append({"role": role, "content": content})
    return trimmed


def fit_messages_to_budget(
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """
    Ensure messages fit under max_tokens by dropping oldest non-system messages first,
    then truncating system content from the end if still over budget.
    """
    if not messages:
        return messages

    budget = int(max_tokens if max_tokens is not None else MAX_INPUT_TOKENS)
    out = [dict(m) for m in messages]

    def _total() -> int:
        return estimate_messages_tokens(out)

    if _total() <= budget:
        return out

    system_idx = 0 if out and out[0].get("role") == "system" else None
    user_idx = len(out) - 1 if out and out[-1].get("role") == "user" else None

    removable = [
        i for i in range(len(out))
        if i != system_idx and i != user_idx
    ]

    while _total() > budget and len(removable) > 0:
        drop = removable.pop(0)
        out.pop(drop)
        removable = [i - 1 if i > drop else i for i in removable]

    if _total() <= budget or system_idx is None:
        return out

    sys_msg = out[system_idx]
    content = str(sys_msg.get("content") or "")
    while _total() > budget and len(content) > 200:
        content = truncate_content(content, int(len(content) * 0.85))
        sys_msg["content"] = content
        out[system_idx] = sys_msg

    return out
