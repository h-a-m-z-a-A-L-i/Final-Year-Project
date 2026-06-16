"""Debug logging for ReAct tool_messages before/after fit_messages_to_budget."""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from typing import Any

try:
    from .context_budget import estimate_messages_tokens, fit_react_messages_to_budget
except Exception:
    from context_budget import estimate_messages_tokens, fit_react_messages_to_budget

_CONTENT_PREVIEW = 2000


def react_call2_debug_enabled() -> bool:
    return os.environ.get("REACT_DEBUG_CALL2", "").strip().lower() in ("1", "true", "yes")


def _message_fingerprint(msg: dict[str, Any], index: int) -> str:
    role = str(msg.get("role") or "")
    content = str(msg.get("content") or "")
    tool_calls = msg.get("tool_calls") or []
    tc_ids = []
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_ids.append(str(tc.get("id") or ""))
    preview = content[:120].replace("\n", " ")
    return f"#{index}|{role}|tc={len(tc_ids)}|{preview}"


def _print_message_list(messages: list[dict[str, Any]], *, header: str) -> None:
    print(header, file=sys.stderr, flush=True)
    print("=" * len(header), file=sys.stderr, flush=True)
    for i, msg in enumerate(messages):
        role = str(msg.get("role") or "")
        content = msg.get("content")
        if content is None:
            content_str = ""
        elif isinstance(content, str):
            content_str = content
        else:
            content_str = str(content)
        preview = content_str[:_CONTENT_PREVIEW]
        print(i, file=sys.stderr, flush=True)
        print(role, file=sys.stderr, flush=True)
        print(preview, file=sys.stderr, flush=True)
        if msg.get("tool_calls"):
            print(f"[tool_calls count={len(msg.get('tool_calls') or [])}]", file=sys.stderr, flush=True)
        print("---", file=sys.stderr, flush=True)


def apply_fit_messages_to_budget_with_debug(
    messages: list[dict[str, Any]],
    *,
    round_idx: int,
    original_user_prompt: str = "",
    pre_trim_messages: list[dict[str, Any]] | None = None,
    removed_fps: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    When REACT_DEBUG_CALL2=1 and round_idx==1 (LLM Call #2), log before/after ReAct trim.
    Messages are already trimmed by fit_react_messages_to_budget in streaming.py.
    """
    if not (react_call2_debug_enabled() and round_idx == 1):
        return messages

    before = deepcopy(pre_trim_messages if pre_trim_messages is not None else messages)
    after = list(messages)
    tokens_before = estimate_messages_tokens(before)
    tokens_after = estimate_messages_tokens(after)

    before_fps = [_message_fingerprint(m, i) for i, m in enumerate(before)]
    after_fps = [_message_fingerprint(m, i) for i, m in enumerate(after)]
    disappeared = list(removed_fps or [])
    if not disappeared:
        after_set = set(after_fps)
        disappeared = [fp for fp in before_fps if fp not in after_set]

    task_marker = str(original_user_prompt or "").strip()
    task_in_before = any(
        task_marker and task_marker in str(m.get("content") or "")
        for m in before
    )
    task_in_after = any(
        task_marker and task_marker in str(m.get("content") or "")
        for m in after
    )

    _print_message_list(before, header="REACT_DEBUG LLM Call #2 — BEFORE fit_react_messages_to_budget()")
    _print_message_list(after, header="REACT_DEBUG LLM Call #2 — AFTER fit_react_messages_to_budget()")

    print(
        f"REACT_DEBUG tokens_before={tokens_before} tokens_after={tokens_after} "
        f"tokens_removed={tokens_before - tokens_after}",
        file=sys.stderr,
        flush=True,
    )
    print(
        f"REACT_DEBUG messages_before={len(before)} messages_after={len(after)} "
        f"messages_removed={len(before) - len(after)}",
        file=sys.stderr,
        flush=True,
    )
    print("REACT_DEBUG disappeared fingerprints:", file=sys.stderr, flush=True)
    for fp in disappeared:
        print(f"  - {fp}", file=sys.stderr, flush=True)
    print(
        f"REACT_DEBUG original_user_survived={task_in_after} "
        f"(present_before={task_in_before})",
        file=sys.stderr,
        flush=True,
    )

    diff = {
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "tokens_removed": tokens_before - tokens_after,
        "messages_before": len(before),
        "messages_after": len(after),
        "messages_removed": len(before) - len(after),
        "disappeared": disappeared,
        "original_user_survived": task_in_after,
        "original_user_present_before": task_in_before,
        "before_fingerprints": before_fps,
        "after_fingerprints": after_fps,
    }
    print(
        "REACT_DEBUG diff_json=" + json.dumps(diff, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )

    return after
