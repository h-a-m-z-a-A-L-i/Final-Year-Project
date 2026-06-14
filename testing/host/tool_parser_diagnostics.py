"""Parser acceptance diagnostics for <agent_tool_batch> text tool mode."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT
    from .agentic_text_tools import (
        _BATCH_RE,
        _FENCE_RE,
        _collect_batch_bodies,
        parse_text_tool_batch_result,
    )
    from .tool_parser_recovery import collect_batch_bodies, parse_json_body
except Exception:
    from config import DATA_ROOT
    from agentic_text_tools import (
        _BATCH_RE,
        _FENCE_RE,
        _collect_batch_bodies,
        parse_text_tool_batch_result,
    )
    from tool_parser_recovery import collect_batch_bodies, parse_json_body

PARSER_FAILURE_LOG = DATA_ROOT / "logs" / "agent_tool_parser_failures.jsonl"
_LOG_LOCK = threading.Lock()

_OPEN_TAG_RE = re.compile(r"<agent_tool_batch\b[^>]*>", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"</agent_tool_batch\s*>", re.IGNORECASE)
_JSON_ARRAY_RE = re.compile(r"\[\s*\{", re.MULTILINE)


def _snippet(text: str, *, head: int = 500, tail: int = 500) -> tuple[str, str]:
    s = str(text or "")
    return s[:head], s[-tail:] if len(s) > tail else s


def diagnose_text_tool_parse(text: str) -> dict[str, Any]:
    """Structural diagnostics for a raw model response (read-only)."""
    raw = str(text or "")
    opening = bool(_OPEN_TAG_RE.search(raw))
    closing = bool(_CLOSE_TAG_RE.search(raw))
    batch_tag_found = opening or closing
    bodies, batch_count, _recovery = collect_batch_bodies(raw)
    tag_bodies = [m.group(1).strip() for m in _BATCH_RE.finditer(raw)]
    fence_fallback_used = bool(bodies) and not tag_bodies and bool(_FENCE_RE.search(raw))

    json_array_found = bool(_JSON_ARRAY_RE.search(raw))
    json_parse_success = False
    json_parse_errors: list[str] = []
    parsed_payload_types: list[str] = []

    for i, body in enumerate(bodies):
        payload, err, _ = parse_json_body(body)
        if err:
            json_parse_errors.append(f"batch_{i + 1}: {err}")
        else:
            json_parse_success = True
            if isinstance(payload, list):
                parsed_payload_types.append("array")
            elif isinstance(payload, dict):
                parsed_payload_types.append("object")
            else:
                parsed_payload_types.append(type(payload).__name__)

    parse_result = parse_text_tool_batch_result(raw, action_required=True)
    tool_count = len(parse_result.tool_calls)

    unclosed = opening and not closing
    non_array_json = bool(
        json_parse_errors
        and any("Expecting" in e or "delimiter" in e or "Extra data" in e for e in json_parse_errors)
    )
    unicode_quote_hint = any(
        "\u201c" in raw or "\u201d" in raw or "\u2018" in raw or "\u2019" in raw
        for _ in [0]
    )

    return {
        "batch_tag_found": batch_tag_found,
        "opening_tag_found": opening,
        "closing_tag_found": closing,
        "unclosed_batch_tag": unclosed,
        "batch_bodies_extracted": len(bodies),
        "batch_count": batch_count,
        "fence_fallback_used": fence_fallback_used,
        "json_array_found": json_array_found,
        "json_parse_success": json_parse_success,
        "json_parse_errors": json_parse_errors,
        "parsed_payload_types": parsed_payload_types,
        "tool_count_detected": tool_count,
        "unknown_tools": list(parse_result.unknown_tools),
        "parse_errors": list(parse_result.parse_errors),
        "multiple_batches": parse_result.multiple_batches,
        "unicode_smart_quotes_present": unicode_quote_hint,
        "non_array_json_likely": non_array_json,
        "raw_length": len(raw),
    }


def derive_parser_reason(
    diagnostics: dict[str, Any],
    *,
    parse_result: Any | None = None,
) -> str:
    """Single primary reason code for a zero-tool parse."""
    pr = parse_result
    tool_count = int(diagnostics.get("tool_count_detected") or 0)
    if tool_count > 0:
        return "PARSE_OK"

    if not diagnostics.get("batch_tag_found") and not diagnostics.get("fence_fallback_used"):
        if diagnostics.get("unicode_smart_quotes_present"):
            return "PROSE_ONLY_NO_TAG"
        return "NO_BATCH_TAG"

    if diagnostics.get("unclosed_batch_tag"):
        return "UNCLOSED_BATCH_TAG"

    if diagnostics.get("opening_tag_found") and not diagnostics.get("batch_bodies_extracted"):
        return "TAG_PRESENT_BODY_NOT_EXTRACTED"

    if diagnostics.get("unknown_tools") and not tool_count:
        return "UNKNOWN_TOOL_ONLY"

    if diagnostics.get("parse_errors") and not diagnostics.get("json_parse_success"):
        errs = " ".join(diagnostics.get("json_parse_errors") or [])
        if "Extra data" in errs:
            return "NON_ARRAY_JSON"
        if diagnostics.get("unicode_smart_quotes_present"):
            return "UNICODE_QUOTES_JSON_FAIL"
        return "JSON_PARSE_ERROR"

    if diagnostics.get("json_parse_success") and tool_count == 0:
        if diagnostics.get("parsed_payload_types") == ["array"] or "array" in (diagnostics.get("parsed_payload_types") or []):
            return "EMPTY_ARRAY_OR_INVALID_ITEMS"
        return "VALID_JSON_ZERO_TOOLS"

    if diagnostics.get("fence_fallback_used") and not diagnostics.get("json_parse_success"):
        return "MARKDOWN_FENCE_PARSE_FAIL"

    if diagnostics.get("batch_tag_found") and diagnostics.get("json_array_found") is False:
        return "NO_JSON_ARRAY_IN_BATCH"

    return "PROSE_BEFORE_OR_AFTER_BATCH_UNPARSEABLE"


def build_parser_failure_record(
    *,
    goal: str,
    round_idx: int,
    raw_output: str,
    action_required: bool = True,
    parse_result: Any | None = None,
    session_id: str | None = None,
    notebook_url: str | None = None,
    source: str = "react_round",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diagnostics = diagnose_text_tool_parse(raw_output)
    if parse_result is None:
        parse_result = parse_text_tool_batch_result(raw_output)
    parsed_tool_count = len(parse_result.tool_calls)
    first_500, last_500 = _snippet(raw_output)
    parser_reason = derive_parser_reason(diagnostics, parse_result=parse_result)

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "goal": str(goal or "").strip(),
        "round": int(round_idx),
        "action_required": bool(action_required),
        "parsed_tool_count": parsed_tool_count,
        "raw_output": raw_output,
        "parser_reason": parser_reason,
        "first_500_chars": first_500,
        "last_500_chars": last_500,
        "diagnostics": diagnostics,
        "parse_feedback": (
            parse_result.to_feedback_dict()
            if hasattr(parse_result, "to_feedback_dict")
            else None
        ),
        "session_id": session_id,
        "notebook_url": notebook_url,
        "source": source,
    }
    if extra:
        record.update(extra)
    return record


def append_parser_failure_record(record: dict[str, Any]) -> None:
    PARSER_FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _LOG_LOCK:
        with PARSER_FAILURE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def log_parser_failure_if_applicable(
    *,
    goal: str,
    round_idx: int,
    raw_output: str,
    parsed_tool_count: int,
    action_required: bool,
    parse_result: Any | None = None,
    session_id: str | None = None,
    notebook_url: str | None = None,
    source: str = "react_round",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not action_required or parsed_tool_count != 0:
        return None
    record = build_parser_failure_record(
        goal=goal,
        round_idx=round_idx,
        raw_output=raw_output,
        action_required=action_required,
        parse_result=parse_result,
        session_id=session_id,
        notebook_url=notebook_url,
        source=source,
        extra=extra,
    )
    append_parser_failure_record(record)
    return record
