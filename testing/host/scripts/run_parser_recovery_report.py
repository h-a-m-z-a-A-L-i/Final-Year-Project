#!/usr/bin/env python3
"""Generate parser_recovery_report.json with recovery acceptance metrics."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_DIR))

from agentic_text_tools import parse_text_tool_batch_result
from tool_parser_recovery import collect_batch_bodies, parse_json_body

OUT = HOST_DIR / "data" / "logs" / "parser_recovery_report.json"
REFUSAL_LOG = HOST_DIR / "data" / "logs" / "agent_tool_refusal.jsonl"

_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"


def _batch(*tools: dict) -> str:
    return (
        "<agent_tool_batch>\n"
        + json.dumps(list(tools))
        + "\n</agent_tool_batch>"
    )


def _positive_cases() -> list[dict]:
    t1 = {"tool": "run_cell", "args": {"cell_index": 1, "url": _URL}}
    return [
        {"id": "strict_clean", "text": _batch(t1), "action_required": False},
        {
            "id": "unclosed_tag",
            "text": f'prose<agent_tool_batch>[{json.dumps(t1)}]',
            "action_required": True,
        },
        {
            "id": "smart_quotes",
            "text": '<agent_tool_batch>[{"tool": “run_cell”, "args": {"cell_index": 1, "url": "' + _URL + '"}}]</agent_tool_batch>',
            "action_required": False,
        },
        {
            "id": "non_array",
            "text": (
                "<agent_tool_batch>\n"
                '{"tool":"run_cell","args":{"cell_index":1,"url":"' + _URL + '"}},\n'
                '{"tool":"run_cell","args":{"cell_index":2,"url":"' + _URL + '"}}\n'
                "</agent_tool_batch>"
            ),
            "action_required": False,
        },
        {
            "id": "bare_json",
            "text": 'Will run.{"tool":"run_cell","args":{"cell_index":1,"url":"' + _URL + '"}}',
            "action_required": True,
        },
    ]


def _negative_cases() -> list[dict]:
    return [
        {"id": "prose_only", "text": "Fix cell 30 by editing the histogram.", "action_required": True},
        {"id": "arbitrary_json", "text": '{"version": 1, "args": {"x": 1}}', "action_required": True},
        {"id": "tool_no_args", "text": '{"tool": "run_cell"}', "action_required": True},
        {"id": "malformed", "text": "<agent_tool_batch>{bad json</agent_tool_batch>", "action_required": True},
    ]


def _cell30_samples() -> list[dict]:
    if not REFUSAL_LOG.exists():
        return []
    out: list[dict] = []
    for line in REFUSAL_LOG.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        goal = str(row.get("goal") or "").lower()
        if "cell 30" not in goal or row.get("source") != "react_round":
            continue
        if int(row.get("parsed_tool_count") or 0) > 0:
            continue
        out.append(
            {
                "id": f"cell30_round_{row.get('round')}",
                "text": str(row.get("raw_model_response") or ""),
                "action_required": True,
            }
        )
    return out


def _evaluate(cases: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for case in cases:
        text = case["text"]
        action_required = bool(case.get("action_required"))
        result = parse_text_tool_batch_result(text, action_required=action_required)
        rows.append(
            {
                "case_id": case["id"],
                "accepted": len(result.tool_calls) > 0,
                "tool_count": len(result.tool_calls),
                "recovery_used": result.recovery_used,
                "recovery_methods": list(result.recovery_methods),
                "parse_errors": list(result.parse_errors),
            }
        )
    return rows


def main() -> int:
    positive = _positive_cases() + _cell30_samples()
    negative = _negative_cases()

    pos_rows = _evaluate(positive)
    neg_rows = _evaluate(negative)

    accepted_without = sum(1 for r in pos_rows if r["accepted"] and not r["recovery_used"])
    accepted_with = sum(1 for r in pos_rows if r["accepted"] and r["recovery_used"])
    pos_accepted = sum(1 for r in pos_rows if r["accepted"])
    false_positives = sum(1 for r in neg_rows if r["accepted"])
    neg_total = len(neg_rows)

    cell30_rows = [r for r in pos_rows if str(r["case_id"]).startswith("cell30")]
    cell30_success = sum(1 for r in cell30_rows if r["accepted"])

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "accepted_without_recovery": accepted_without,
            "accepted_with_recovery": accepted_with,
            "positive_cases_total": len(pos_rows),
            "positive_acceptance_rate": round(pos_accepted / len(pos_rows), 4) if pos_rows else 0.0,
            "recovery_rate": round(accepted_with / len(pos_rows), 4) if pos_rows else 0.0,
            "false_positive_count": false_positives,
            "false_positive_rate": round(false_positives / neg_total, 4) if neg_total else 0.0,
            "cell30_samples_total": len(cell30_rows),
            "cell30_samples_accepted": cell30_success,
            "cell30_success_rate": round(cell30_success / len(cell30_rows), 4) if cell30_rows else 0.0,
        },
        "positive_case_results": pos_rows,
        "negative_case_results": neg_rows,
        "cell30_target_met": cell30_success == len(cell30_rows) and len(cell30_rows) > 0,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(json.dumps(report["metrics"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
