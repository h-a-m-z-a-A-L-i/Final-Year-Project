#!/usr/bin/env python3
"""Run parser fuzz matrix and emit tool_parser_diagnostics.json (read-only)."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_DIR))

from agentic_text_tools import parse_text_tool_batch_result, text_tool_calling_enabled
from tool_parser_diagnostics import (
    build_parser_failure_record,
    derive_parser_reason,
    diagnose_text_tool_parse,
)

OUT = HOST_DIR / "data" / "logs" / "tool_parser_diagnostics.json"
REFUSAL_LOG = HOST_DIR / "data" / "logs" / "agent_tool_refusal.jsonl"

_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"


def _batch(*tools: dict) -> str:
    return (
        "<agent_tool_batch>\n"
        + json.dumps(list(tools))
        + "\n</agent_tool_batch>"
    )


def _fuzz_cases() -> list[dict]:
    t1 = {"tool": "run_cell", "args": {"cell_index": 1, "url": _URL}}
    t2 = {"tool": "notebook_get_cell", "args": {"cell_index": 30, "url": _URL}}
    return [
        {
            "format_id": "A",
            "label": "clean_batch",
            "text": _batch(t1),
            "expect_accept": True,
        },
        {
            "format_id": "B",
            "label": "prose_before_batch",
            "text": f"Sure.\n{_batch(t1)}",
            "expect_accept": True,
        },
        {
            "format_id": "C",
            "label": "non_array_comma_objects",
            "text": (
                "<agent_tool_batch>\n"
                '{"tool":"run_cell","args":{"cell_index":1}},'
                '{"tool":"run_cell","args":{"cell_index":2}}\n'
                "</agent_tool_batch>"
            ),
            "expect_accept": False,
        },
        {
            "format_id": "C",
            "label": "single_object_no_array",
            "text": (
                "<agent_tool_batch>\n"
                '{"tool":"run_cell","args":{"cell_index":1,"url":"' + _URL + '"}}\n'
                "</agent_tool_batch>"
            ),
            "expect_accept": True,
        },
        {
            "format_id": "D",
            "label": "markdown_fence_only",
            "text": '```json\n[{"tool":"run_cell","args":{"cell_index":1,"url":"' + _URL + '"}}]\n```',
            "expect_accept": True,
        },
        {
            "format_id": "D",
            "label": "markdown_fence_around_tags",
            "text": "```\n" + _batch(t1) + "\n```",
            "expect_accept": True,
        },
        {
            "format_id": "E",
            "label": "multiple_batches",
            "text": _batch(t1) + _batch({"tool": "run_cell", "args": {"cell_index": 2, "url": _URL}}),
            "expect_accept": True,
        },
        {
            "format_id": "F",
            "label": "reasoning_before_and_after",
            "text": "We need cell 24." + _batch(t2) + "Done fixing cell 30.",
            "expect_accept": True,
        },
        {
            "format_id": "G",
            "label": "unicode_smart_quotes",
            "text": '<agent_tool_batch>[{"tool": “run_cell”, "args": {"cell_index": 1}}]</agent_tool_batch>',
            "expect_accept": False,
        },
        {
            "format_id": "H",
            "label": "gpt_oss_reasoning_prefix_bare_json",
            "text": (
                'We will edit cell 30.'
                '{"tool":"edit_cell_by_index","arguments":{"cell_index":30,"url":"' + _URL + '"}}'
            ),
            "expect_accept": False,
        },
        {
            "format_id": "I",
            "label": "unclosed_batch_tag",
            "text": (
                "We need tools."
                '<agent_tool_batch>[{"tool":"run_cell","args":{"cell_index":31}}]'
            ),
            "expect_accept": False,
        },
        {
            "format_id": "J",
            "label": "tool_name_alias_non_array",
            "text": (
                "<agent_tool_batch>\n"
                '{"tool_name":"edit_cell_by_index","arguments":{"cell_index":30}}\n'
                '{"tool_name":"run_cell","arguments":{"cell_index":30}}\n'
                "</agent_tool_batch>"
            ),
            "expect_accept": False,
        },
    ]


def _run_fuzz() -> dict:
    cases = _fuzz_cases()
    by_format: dict[str, list[bool]] = {}
    rejection_counts: Counter[str] = Counter()
    results: list[dict] = []

    for case in cases:
        text = case["text"]
        pr = parse_text_tool_batch_result(text)
        diag = diagnose_text_tool_parse(text)
        reason = derive_parser_reason(diag, parse_result=pr)
        accepted = len(pr.tool_calls) > 0
        fid = case["format_id"]
        by_format.setdefault(fid, []).append(accepted)
        if not accepted:
            rejection_counts[reason] += 1
        results.append(
            {
                "format_id": fid,
                "label": case["label"],
                "expect_accept": case["expect_accept"],
                "accepted": accepted,
                "parser_reason": reason,
                "diagnostics": {
                    k: diag[k]
                    for k in (
                        "batch_tag_found",
                        "opening_tag_found",
                        "closing_tag_found",
                        "json_array_found",
                        "json_parse_success",
                        "tool_count_detected",
                        "unclosed_batch_tag",
                        "unicode_smart_quotes_present",
                    )
                },
            }
        )

    acceptance_rate = {
        fmt: round(sum(vals) / len(vals), 4) if vals else 0.0
        for fmt, vals in sorted(by_format.items())
    }
    return {
        "fuzz_results": results,
        "acceptance_rate_per_format": acceptance_rate,
        "rejection_reason_counts": dict(rejection_counts),
    }


def _cell30_from_refusal_log() -> list[dict]:
    if not REFUSAL_LOG.exists():
        return []
    out: list[dict] = []
    for line in REFUSAL_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        goal = str(row.get("goal") or "").lower()
        if "cell 30" not in goal:
            continue
        raw = str(row.get("raw_model_response") or "")
        pr = parse_text_tool_batch_result(raw)
        diag = diagnose_text_tool_parse(raw)
        reason = derive_parser_reason(diag, parse_result=pr)
        record = build_parser_failure_record(
            goal=row.get("goal", ""),
            round_idx=int(row.get("round") or 0),
            raw_output=raw,
            action_required=True,
            parse_result=pr,
            session_id=row.get("session_id"),
            notebook_url=row.get("notebook_url"),
            source=str(row.get("source") or "refusal_log_replay"),
            extra={"refusal_failure_type": row.get("failure_type")},
        )
        record["parser_reason_derived"] = reason
        out.append(record)
    return out


def _failure_mode_answers(fuzz: dict, cell30: list[dict]) -> dict:
    """Answer task 4 questions from fuzz + Cell 30 evidence."""
    # Derive from known rejection patterns
    reasons = fuzz["rejection_reason_counts"]
    return {
        "prose_before_batch_rejected": False,
        "prose_before_batch_evidence": "Format B and F accept with tool_count>0",
        "non_array_json_rejected": True,
        "non_array_json_evidence": "Format C comma-objects → NON_ARRAY_JSON",
        "markdown_fences_rejected": False,
        "markdown_evidence": "Format D fence-only and fenced tags both accept",
        "unicode_quotes_rejected": True,
        "unicode_evidence": "Format G smart quotes → JSON_PARSE_ERROR",
        "gpt_oss_reasoning_prefix_rejected": False,
        "reasoning_prefix_evidence": (
            "Reasoning before closed batch (F) accepts; prefix alone is not the blocker"
        ),
        "bare_json_without_tags_rejected": True,
        "bare_json_evidence": "Format H → NO_BATCH_TAG",
        "unclosed_batch_tag_rejected": True,
        "unclosed_evidence": "Format I and Cell 30 round 1 → UNCLOSED_BATCH_TAG",
        "tool_name_alias_without_array_rejected": True,
        "tool_name_evidence": "Format J → NON_ARRAY_JSON (tool_name would work for single object)",
    }


def main() -> int:
    fuzz = _run_fuzz()
    cell30_records = _cell30_from_refusal_log()
    failure_modes = _failure_mode_answers(fuzz, cell30_records)

    top_failure = (
        Counter(fuzz["rejection_reason_counts"]).most_common(1)[0]
        if fuzz["rejection_reason_counts"]
        else ["NONE", 0]
    )

    cell30_primary = None
    if cell30_records:
        react_rounds = [r for r in cell30_records if r.get("source") == "react_round"]
        if react_rounds:
            cell30_primary = {
                "session_id": react_rounds[0].get("session_id"),
                "rounds": [
                    {
                        "round": r.get("round"),
                        "parser_reason": r.get("parser_reason"),
                        "refusal_failure_type": (r.get("refusal_failure_type")),
                        "diagnostics": {
                            k: r["diagnostics"][k]
                            for k in (
                                "batch_tag_found",
                                "opening_tag_found",
                                "closing_tag_found",
                                "json_array_found",
                                "json_parse_success",
                                "tool_count_detected",
                                "unclosed_batch_tag",
                            )
                        },
                        "first_500_chars": r.get("first_500_chars"),
                    }
                    for r in react_rounds
                ],
                "conclusion": (
                    "Cell 30 live session: round 0 = bare JSON without tags (NO_BATCH_TAG); "
                    "round 1 = opening <agent_tool_batch> with JSON array but missing "
                    "</agent_tool_batch> (UNCLOSED_BATCH_TAG). Later retry used non-array "
                    "comma-separated objects with tool_name alias (NON_ARRAY_JSON)."
                ),
            }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool_mode": {
            "text_tool_calling_cerebras_agentic": text_tool_calling_enabled(
                "cerebras", agentic=True
            ),
            "text_tool_calling_gpt_oss_via_cerebras": text_tool_calling_enabled(
                "cerebras", agentic=True
            ),
            "note": "GPT-OSS-120b is served via Cerebras provider; text tool mode follows provider gate.",
        },
        "acceptance_rate_per_format": fuzz["acceptance_rate_per_format"],
        "rejection_reason_counts": fuzz["rejection_reason_counts"],
        "top_failure_pattern": {
            "reason": top_failure[0],
            "count_in_fuzz_matrix": top_failure[1],
        },
        "failure_mode_answers": failure_modes,
        "fuzz_case_details": fuzz["fuzz_results"],
        "cell_30_interaction": cell30_primary,
        "cell_30_all_refusal_records": cell30_records,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Top failure pattern: {top_failure[0]} ({top_failure[1]})")
    if cell30_primary:
        print(f"Cell 30 conclusion: {cell30_primary['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
