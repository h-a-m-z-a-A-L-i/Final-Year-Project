"""Tolerant recovery layer for <agent_tool_batch> text tool parsing."""

from __future__ import annotations

import json
import re
from typing import Any

_OPEN_BATCH_RE = re.compile(r"<agent_tool_batch\b[^>]*>", re.IGNORECASE)
_CLOSE_BATCH_RE = re.compile(r"</agent_tool_batch\s*>", re.IGNORECASE)
_STRICT_BATCH_RE = re.compile(
    r"<agent_tool_batch>\s*([\s\S]*?)\s*</agent_tool_batch>",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\[\s*\{[\s\S]*?\}\s*\])\s*```",
    re.IGNORECASE,
)
_NEXT_XML_TAG_RE = re.compile(r"<\s*/?\w+")
_BARE_TOOL_MARKER_RE = re.compile(
    r'[\[{]\s*"(?:tool|tool_name)"\s*:',
    re.IGNORECASE,
)
_ADJACENT_OBJECT_RE = re.compile(r"}\s*{")

_SMART_QUOTE_MAP = str.maketrans(
    {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
)


def normalize_smart_quotes(text: str) -> tuple[str, bool]:
    raw = str(text or "")
    normalized = raw.translate(_SMART_QUOTE_MAP)
    return normalized, normalized != raw


def _has_tool_fields(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    name = obj.get("tool") or obj.get("tool_name") or obj.get("name")
    if not name or not str(name).strip():
        fn = obj.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
    if not name or not str(name).strip():
        return False
    args = obj.get("args")
    if args is None:
        args = obj.get("arguments")
    return args is not None


def _payload_has_tool_candidates(payload: Any) -> bool:
    if isinstance(payload, dict):
        if isinstance(payload.get("tool_calls"), list):
            items = payload["tool_calls"]
        elif isinstance(payload.get("tools"), list):
            items = payload["tools"]
        else:
            return _has_tool_fields(payload)
        return bool(items) and all(_has_tool_fields(it) for it in items if isinstance(it, dict))
    if isinstance(payload, list):
        if not payload:
            return False
        return all(_has_tool_fields(it) for it in payload if isinstance(it, dict))
    return False


def _extract_balanced_json(text: str, start: int) -> str | None:
    if start >= len(text) or text[start] not in "{[":
        return None
    stack = [text[start]]
    pairs = {"[": "]", "{": "}"}
    in_string = False
    escape = False
    for i in range(start + 1, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack or pairs[stack[-1]] != ch:
                return None
            stack.pop()
            if not stack:
                return text[start : i + 1]
    return None


def _extract_unclosed_batch_bodies(text: str) -> list[str]:
    """Recover batch body when opening tag exists without closing tag."""
    raw = str(text or "")
    if not _OPEN_BATCH_RE.search(raw) or _CLOSE_BATCH_RE.search(raw):
        return []
    bodies: list[str] = []
    for open_match in _OPEN_BATCH_RE.finditer(raw):
        tail = raw[open_match.end() :]
        next_tag = _NEXT_XML_TAG_RE.search(tail)
        body = tail[: next_tag.start()].strip() if next_tag else tail.strip()
        if body:
            bodies.append(body)
    return bodies


def collect_batch_bodies(text: str) -> tuple[list[str], int, list[str]]:
    """
    Collect JSON bodies from strict tags, fence fallback, or unclosed-tag recovery.
    Returns (bodies, batch_count, recovery_methods).
    """
    raw = str(text or "")
    recovery: list[str] = []
    bodies = [m.group(1).strip() for m in _STRICT_BATCH_RE.finditer(raw)]
    if bodies:
        return bodies, len(bodies), recovery

    fence = _FENCE_RE.search(raw)
    if fence:
        return [fence.group(1).strip()], 1, recovery

    unclosed = _extract_unclosed_batch_bodies(raw)
    if unclosed:
        recovery.append("unclosed_tag")
        return unclosed, len(unclosed), recovery

    return [], 0, recovery


def _try_non_array_wrap(body: str) -> tuple[Any | None, str | None]:
    """Wrap comma-separated or adjacent JSON objects into an array."""
    candidates = [body.strip()]
    if _ADJACENT_OBJECT_RE.search(body):
        candidates.append(_ADJACENT_OBJECT_RE.sub("},{", body.strip()))
    last_err: str | None = None
    for candidate in candidates:
        wrapped = f"[{candidate}]"
        try:
            payload = json.loads(wrapped)
            if _payload_has_tool_candidates(payload):
                return payload, None
        except json.JSONDecodeError as exc:
            last_err = str(exc)
    return None, last_err or "non_array_wrap failed"


def parse_json_body(raw_json: str) -> tuple[Any | None, str | None, list[str]]:
    """
    Parse batch JSON with smart-quote normalization and non-array wrap recovery.
    Returns (payload, error, recovery_methods).
    """
    recovery: list[str] = []
    body = str(raw_json or "").strip()
    if not body:
        return None, "empty body", recovery

    normalized, changed = normalize_smart_quotes(body)
    if changed:
        recovery.append("smart_quotes")
        body = normalized

    try:
        return json.loads(body), None, recovery
    except json.JSONDecodeError as exc:
        err = str(exc)
        if "Extra data" in err:
            payload, wrap_err = _try_non_array_wrap(body)
            if payload is not None:
                recovery.append("non_array_wrap")
                return payload, None, recovery
            if wrap_err:
                err = wrap_err
        return None, err, recovery


def extract_bare_tool_bodies(text: str) -> tuple[list[str], list[str]]:
    """Find bare tool JSON (no batch tags) when action_required recovery is allowed."""
    raw = str(text or "")
    if _OPEN_BATCH_RE.search(raw):
        return [], []
    recovery: list[str] = []
    bodies: list[str] = []
    seen: set[str] = set()
    for match in _BARE_TOOL_MARKER_RE.finditer(raw):
        start = match.start()
        if raw[start] not in "{[":
            # marker matched inside array; back up to [
            bracket = raw.rfind("[", 0, start)
            brace = raw.rfind("{", 0, start)
            start = max(bracket, brace)
            if start < 0 or raw[start] not in "{[":
                continue
        fragment = _extract_balanced_json(raw, start)
        if not fragment or fragment in seen:
            continue
        payload, err, _ = parse_json_body(fragment)
        if err or not _payload_has_tool_candidates(payload):
            continue
        seen.add(fragment)
        bodies.append(fragment)
    if bodies:
        recovery.append("bare_json")
    return bodies, recovery
