"""Custom text-format tool calls for agentic mode (Cerebras chat-only batching)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

try:
    from .agentic_text_tools_types import TextToolParseResult
    from .tool_parser_recovery import (
        collect_batch_bodies,
        extract_bare_tool_bodies,
        parse_json_body,
    )
except Exception:
    from agentic_text_tools_types import TextToolParseResult
    from tool_parser_recovery import (
        collect_batch_bodies,
        extract_bare_tool_bodies,
        parse_json_body,
    )

_BATCH_RE = re.compile(
    r"<agent_tool_batch>\s*([\s\S]*?)\s*</agent_tool_batch>",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\[\s*\{[\s\S]*?\}\s*\])\s*```",
    re.IGNORECASE,
)
_UNCLOSED_BATCH_RE = re.compile(
    r"<agent_tool_batch\b[^>]*>[\s\S]*",
    re.IGNORECASE,
)


def text_tool_calling_enabled(provider: str, *, agentic: bool) -> bool:
    if not agentic:
        return False
    import os

    env = os.environ.get("AGENTIC_TEXT_TOOLS", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    try:
        from .llm_provider import cerebras_uses_text_tool_batch, normalize_provider
    except Exception:
        from llm_provider import cerebras_uses_text_tool_batch, normalize_provider
    if normalize_provider(provider) != "cerebras":
        return False
    # Default: text batch only for gpt-oss (single native tool_call). GLM uses API tools.
    return cerebras_uses_text_tool_batch()


def _allowed_tool_names() -> set[str]:
    try:
        from .tool_registry import BROWSER_TOOL_NAMES
        from .local_notebook_tools import LLM_LOCAL_TOOL_NAMES
    except Exception:
        from tool_registry import BROWSER_TOOL_NAMES
        from local_notebook_tools import LLM_LOCAL_TOOL_NAMES
    return set(LLM_LOCAL_TOOL_NAMES) | set(BROWSER_TOOL_NAMES)


def _tool_name_from_raw(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    name = str(
        raw.get("tool") or raw.get("tool_name") or raw.get("name") or ""
    ).strip()
    if not name:
        fn = raw.get("function")
        if isinstance(fn, dict):
            name = str(fn.get("name") or "").strip()
    return name


def _normalize_entry(raw: Any, idx: int) -> dict[str, Any] | None:
    name = _tool_name_from_raw(raw)
    if not name or name not in _allowed_tool_names():
        return None
    if not isinstance(raw, dict):
        return None
    args = raw.get("args")
    if args is None:
        args = raw.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return {
        "id": str(raw.get("id") or f"text_tool_{idx}_{uuid.uuid4().hex[:8]}"),
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _payload_to_items(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("tool_calls"), list):
            return payload["tool_calls"]
        if isinstance(payload.get("tools"), list):
            return payload["tools"]
        return [payload]
    if isinstance(payload, list):
        return payload
    return []


def _collect_batch_bodies(text: str) -> tuple[list[str], int]:
    """Backward-compatible strict body collection."""
    body = str(text or "")
    bodies = [m.group(1).strip() for m in _BATCH_RE.finditer(body)]
    batch_count = len(bodies)
    if not bodies:
        fence = _FENCE_RE.search(body)
        if fence:
            bodies = [fence.group(1).strip()]
            batch_count = 1
    return bodies, batch_count


def parse_text_tool_batch_result(
    text: str,
    *,
    action_required: bool = False,
) -> TextToolParseResult:
    """
    Parse all <agent_tool_batch> blocks (merged safely) plus unknown-tool detection.
    Applies tolerant recovery (unclosed tags, smart quotes, non-array wrap, bare JSON).
    """
    result = TextToolParseResult()
    raw_text = str(text or "")
    all_recovery: list[str] = []

    bodies, batch_count, body_recovery = collect_batch_bodies(raw_text)
    all_recovery.extend(body_recovery)

    if not bodies and action_required:
        bare_bodies, bare_recovery = extract_bare_tool_bodies(raw_text)
        if bare_bodies:
            bodies = bare_bodies
            batch_count = len(bodies)
            all_recovery.extend(bare_recovery)

    result.batch_count = batch_count
    result.multiple_batches = batch_count > 1

    if not bodies:
        return result

    all_items: list[Any] = []
    for i, raw_json in enumerate(bodies):
        payload, err, json_recovery = parse_json_body(raw_json)
        all_recovery.extend(json_recovery)
        if err:
            result.parse_errors.append(f"batch_{i + 1}: {err}")
            continue
        if payload is None:
            result.parse_errors.append(f"batch_{i + 1}: empty payload")
            continue
        all_items.extend(_payload_to_items(payload))

    seen_unknown: set[str] = set()
    for idx, item in enumerate(all_items):
        name = _tool_name_from_raw(item)
        if not name:
            result.parse_errors.append(f"item_{idx}: missing tool name")
            continue
        if name not in _allowed_tool_names():
            if name not in seen_unknown:
                seen_unknown.add(name)
                result.unknown_tools.append(name)
            continue
        normalized = _normalize_entry(item, idx)
        if normalized:
            result.tool_calls.append(normalized)

    if all_recovery:
        result.recovery_used = True
        result.recovery_methods = list(dict.fromkeys(all_recovery))

    return result


def parse_text_tool_batch(text: str, *, action_required: bool = False) -> list[dict[str, Any]]:
    """Backward-compatible: returns tool_calls only."""
    return parse_text_tool_batch_result(text, action_required=action_required).tool_calls


def strip_tool_batch_from_text(text: str) -> str:
    cleaned = _BATCH_RE.sub("", str(text or ""))
    cleaned = _UNCLOSED_BATCH_RE.sub("", cleaned)
    cleaned = _FENCE_RE.sub("", cleaned)
    return cleaned.strip()


def inject_tool_defaults(
    tool_calls: list[dict],
    *,
    url: str,
    tab_id: int | None,
) -> list[dict]:
    out: list[dict] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = dict(tc.get("function") or {})
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        except Exception:
            args = {}
        try:
            from .bot_tool_utils import coerce_notebook_tool_session
        except Exception:
            from bot_tool_utils import coerce_notebook_tool_session
        tool_name = str(fn.get("name") or "").strip()
        args = coerce_notebook_tool_session(
            args,
            session_url=url,
            session_tab_id=tab_id if isinstance(tab_id, int) else None,
            tool_name=tool_name,
        )
        out.append(
            {
                **tc,
                "function": {
                    **fn,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        )
    return out


def format_tool_batch_example(url: str) -> str:
    example = [
        {"tool": "insert_cell", "args": {"index": 2, "direction": "below", "url": url}},
        {
            "tool": "edit_cell_by_index",
            "args": {"cell_index": 3, "content": "print('hi')", "url": url},
        },
        {"tool": "run_cell", "args": {"cell_index": 3, "url": url}},
    ]
    return (
        "<agent_tool_batch>\n"
        + json.dumps(example, indent=2, ensure_ascii=False)
        + "\n</agent_tool_batch>"
    )
