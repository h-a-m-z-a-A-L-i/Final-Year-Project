"""Token budgeting for stateless API calls: trim history and fit messages to model limits."""

from __future__ import annotations

import json
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

try:
    from .agentic_verification import VERIFICATION_MARKER
except Exception:
    try:
        from agentic_verification import VERIFICATION_MARKER
    except Exception:
        VERIFICATION_MARKER = "__react_batch_verification__"

KEEP_LATEST_VERIFICATIONS = 5
REACT_VERIFICATION_SUMMARY = "_react_verification_summary"


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


_API_MESSAGE_KEYS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id"})


def _assistant_tool_call_ids(msg: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict) and tc.get("id"):
            ids.append(str(tc["id"]))
    return ids


def _tool_response_indices_for_batch(
    messages: list[dict[str, Any]],
    assistant_idx: int,
) -> set[int]:
    """Indices of role=tool messages that answer one assistant tool_calls turn."""
    protected: set[int] = set()
    if assistant_idx < 0 or assistant_idx >= len(messages):
        return protected
    expected = _assistant_tool_call_ids(messages[assistant_idx])
    if not expected:
        return protected
    found: set[str] = set()
    j = assistant_idx + 1
    while j < len(messages) and len(found) < len(expected):
        msg = messages[j]
        if msg.get("_react_verification") or VERIFICATION_MARKER in str(msg.get("content") or ""):
            break
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            protected.add(j)
            found.add(str(msg["tool_call_id"]))
            j += 1
            continue
        if msg.get("role") == "assistant":
            break
        if msg.get("role") == "user" and not msg.get("_react_agent_state"):
            break
        j += 1
    return protected


def ensure_tool_call_message_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Repair OpenAI/Cerebras message format: every assistant tool_calls turn must be
    followed by one role=tool message per tool_call_id (insert placeholders if trimmed).
    """
    if not messages:
        return []

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if not isinstance(msg, dict):
            i += 1
            continue
        out.append(dict(msg))
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            expected = _assistant_tool_call_ids(msg)
            found: set[str] = set()
            i += 1
            while i < len(messages) and messages[i].get("role") == "tool" and messages[i].get("tool_call_id"):
                tool_msg = messages[i]
                out.append(dict(tool_msg))
                found.add(str(tool_msg["tool_call_id"]))
                i += 1
            for tid in expected:
                if tid not in found:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "error": "tool result missing from context (trimmed or not recorded)",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
            continue
        i += 1
    return out


def sanitize_tool_message_chain(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Drop orphan role=tool messages (e.g. after ReAct trim removed assistant tool_calls).
    Then repair missing tool responses for each assistant tool_calls turn.
    """
    if not messages:
        return []

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if not isinstance(msg, dict):
            i += 1
            continue
        if msg.get("role") == "tool":
            i += 1
            continue
        out.append(dict(msg))
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            expected = set(_assistant_tool_call_ids(msg))
            i += 1
            while i < len(messages):
                nxt = messages[i]
                if not isinstance(nxt, dict):
                    break
                if nxt.get("_react_verification") or VERIFICATION_MARKER in str(nxt.get("content") or ""):
                    break
                if nxt.get("role") == "tool" and nxt.get("tool_call_id"):
                    tid = str(nxt["tool_call_id"])
                    if tid in expected:
                        out.append(dict(nxt))
                    i += 1
                    continue
                break
            continue
        i += 1
    return ensure_tool_call_message_pairs(out)


def messages_for_api(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Strip internal ReAct markers before provider API calls."""
    repaired = sanitize_tool_message_chain(list(messages or []))
    out: list[dict[str, Any]] = []
    for msg in repaired:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        if not role:
            continue
        clean = {k: msg[k] for k in _API_MESSAGE_KEYS if k in msg and msg[k] is not None}
        clean["role"] = role
        out.append(clean)
    return out


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


def _is_verification_message(msg: dict[str, Any]) -> bool:
    if msg.get("_react_verification"):
        return True
    return VERIFICATION_MARKER in str(msg.get("content") or "")


def _parse_verification_payload(msg: dict[str, Any]) -> dict[str, Any]:
    content = str(msg.get("content") or "")
    if VERIFICATION_MARKER not in content:
        return {}
    try:
        payload = content.split(VERIFICATION_MARKER, 1)[1].strip()
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _extract_verification_stats(compact: dict[str, Any]) -> tuple[list[str], list[int]]:
    action_types: list[str] = []
    cells: list[int] = []

    for item in compact.get("executed") or []:
        if isinstance(item, dict):
            tool = str(item.get("tool") or "").strip()
            if tool:
                action_types.append(tool)
            try:
                ci = item.get("cell_index")
                if ci is not None:
                    cells.append(int(ci))
            except (TypeError, ValueError):
                pass

    for item in compact.get("completed") or []:
        if isinstance(item, dict):
            tool = str(item.get("tool") or item.get("phase") or "run_cell").strip()
            if tool:
                action_types.append(tool)
            try:
                ci = item.get("cell_index")
                if ci is not None:
                    cells.append(int(ci))
            except (TypeError, ValueError):
                pass
        elif item is not None:
            action_types.append(str(item))

    for c in compact.get("target_cells_preview") or []:
        if isinstance(c, dict):
            try:
                ci = c.get("cell_index")
                if ci is not None:
                    cells.append(int(ci))
            except (TypeError, ValueError):
                pass

    try:
        ci = compact.get("cell_index")
        if ci is not None:
            cells.append(int(ci))
    except (TypeError, ValueError):
        pass

    err = compact.get("execution_error")
    if isinstance(err, dict):
        try:
            ci = err.get("cell_index")
            if ci is not None:
                cells.append(int(ci))
        except (TypeError, ValueError):
            pass
        et = err.get("error_type")
        if et:
            action_types.append(f"error:{et}")

    status = str(compact.get("batch_status") or "").strip()
    if status and status not in action_types:
        action_types.append(status)

    return action_types, cells


def _build_verification_summary_message(
    completed_actions_count: int,
    action_types: list[str],
    cells: list[int],
) -> dict[str, Any]:
    recent_types: list[str] = []
    seen_types: set[str] = set()
    for t in reversed(action_types):
        if t not in seen_types:
            seen_types.add(t)
            recent_types.append(t)
        if len(recent_types) >= 8:
            break
    recent_types.reverse()

    recent_cells: list[int] = []
    seen_cells: set[int] = set()
    for c in reversed(cells):
        if c not in seen_cells:
            seen_cells.add(c)
            recent_cells.append(c)
        if len(recent_cells) >= 10:
            break
    recent_cells.reverse()

    lines = [
        "VERIFICATION SUMMARY",
        f"completed_actions_count: {completed_actions_count}",
        f"recent_action_types: {', '.join(recent_types) if recent_types else 'none'}",
        f"recent_cells: {', '.join(str(c) for c in recent_cells) if recent_cells else 'none'}",
    ]
    return {
        "role": "user",
        "content": "\n".join(lines),
        REACT_VERIFICATION_SUMMARY: True,
    }


def compress_react_verification_history(
    messages: list[dict[str, Any]],
    *,
    keep_latest: int = KEEP_LATEST_VERIFICATIONS,
) -> list[dict[str, Any]]:
    """Keep the latest N verification turns; collapse older ones into one summary."""
    if not messages or keep_latest < 0:
        return [dict(m) for m in messages]

    msgs = [dict(m) for m in messages]
    ver_indices = [i for i, m in enumerate(msgs) if _is_verification_message(m)]
    if len(ver_indices) <= keep_latest:
        return msgs

    compress_indices = ver_indices[:-keep_latest]
    drop: set[int] = set(compress_indices)
    action_types: list[str] = []
    cells: list[int] = []

    for vi in compress_indices:
        at, c = _extract_verification_stats(_parse_verification_payload(msgs[vi]))
        action_types.extend(at)
        cells.extend(c)
        k = vi - 1
        while k >= 0 and msgs[k].get("role") == "tool" and msgs[k].get("tool_call_id"):
            drop.add(k)
            k -= 1
        if k >= 0 and msgs[k].get("_react_tool_batch"):
            drop.add(k)

    out = [
        m for i, m in enumerate(msgs)
        if i not in drop and not m.get(REACT_VERIFICATION_SUMMARY)
    ]
    summary = _build_verification_summary_message(len(compress_indices), action_types, cells)

    insert_at = 0
    if out and out[0].get("role") == "system":
        insert_at = 1
    for j in range(insert_at, min(insert_at + 4, len(out))):
        if out[j].get("_react_original_user"):
            insert_at = j + 1
            break

    out.insert(insert_at, summary)
    return out


def _react_protected_indices(
    messages: list[dict[str, Any]],
    *,
    original_user_prompt: str = "",
) -> set[int]:
    """Indices that must survive ReAct trimming: system, goal user, tool batch, verification."""
    protected: set[int] = set()
    if not messages:
        return protected

    if messages[0].get("role") == "system":
        protected.add(0)

    task = str(original_user_prompt or "").strip()
    for i, msg in enumerate(messages):
        if msg.get("_react_original_user") or msg.get("_react_agent_state"):
            protected.add(i)
        if msg.get(REACT_VERIFICATION_SUMMARY):
            protected.add(i)

    ver_indices = [
        i for i, m in enumerate(messages)
        if _is_verification_message(m) and not m.get(REACT_VERIFICATION_SUMMARY)
    ]
    for i in ver_indices[-KEEP_LATEST_VERIFICATIONS:]:
        protected.add(i)

    batch_indices = [i for i, m in enumerate(messages) if m.get("_react_tool_batch")]
    for i in batch_indices[-KEEP_LATEST_VERIFICATIONS:]:
        protected.add(i)
        protected |= _tool_response_indices_for_batch(messages, i)

    if task:
        for i, msg in enumerate(messages):
            if msg.get("role") == "user" and task in str(msg.get("content") or ""):
                protected.add(i)
                break

    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("_react_tool_batch") or (
            messages[i].get("role") == "assistant" and messages[i].get("tool_calls")
        ):
            protected.add(i)
            break

    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("_react_verification") and not messages[i].get(REACT_VERIFICATION_SUMMARY):
            protected.add(i)
            break
        content = str(messages[i].get("content") or "")
        if VERIFICATION_MARKER in content:
            protected.add(i)
            break

    return protected


def fit_react_messages_to_budget(
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    *,
    original_user_prompt: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Fit messages under budget while preserving ReAct-critical turns.
    Drops oldest non-protected messages first (typically middle chat history).
    Returns (fitted_messages, removed_fingerprints).
    """
    if not messages:
        return messages, []

    budget = int(max_tokens if max_tokens is not None else MAX_INPUT_TOKENS)
    out = compress_react_verification_history([dict(m) for m in messages])
    protected = _react_protected_indices(out, original_user_prompt=original_user_prompt)

    def _total() -> int:
        return estimate_messages_tokens(out)

    removed: list[str] = []

    def _fingerprint(idx: int, msg: dict[str, Any]) -> str:
        role = str(msg.get("role") or "")
        preview = str(msg.get("content") or "")[:80].replace("\n", " ")
        return f"#{idx}|{role}|{preview}"

    if _total() <= budget:
        return out, removed

    removable = [i for i in range(len(out)) if i not in protected]
    while _total() > budget and removable:
        drop = removable.pop(0)
        removed.append(_fingerprint(drop, out[drop]))
        out.pop(drop)
        protected = {i if i < drop else i - 1 for i in protected if i != drop}
        removable = [i if i < drop else i - 1 for i in removable if i != drop]

    system_idx = 0 if out and out[0].get("role") == "system" else None
    if _total() > budget and system_idx is not None:
        sys_msg = out[system_idx]
        content = str(sys_msg.get("content") or "")
        while _total() > budget and len(content) > 200:
            content = truncate_content(content, int(len(content) * 0.85))
            sys_msg["content"] = content
            out[system_idx] = sys_msg

    return out, removed
