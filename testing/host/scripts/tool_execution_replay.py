#!/usr/bin/env python3
"""
Offline replay of the tool execution pipeline for audit / root-cause analysis.
Does not invoke browser tools — traces parse → dispatch → executor decision → verification inference.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_DIR))

from agentic_text_tools import parse_text_tool_batch_result
from agentic_batch_executor import should_use_batch_executor, analyze_cell_output
from agentic_action_guard import is_actionable_notebook_request
from tool_execution_audit import (
    STREAMING_HOOKS,
    EXECUTOR_HOOK,
    build_batch_record,
    classify_failure_case,
    infer_pipeline_stop_stage,
    summarize_verification,
    _assistant_claims_success,
    _tool_names_from_calls,
)

NOTEBOOK_PATH = (
    HOST_DIR
    / "data"
    / "notebooks"
    / "persistent"
    / "https___www_kaggle_com_code_codekey_testing_ol_edit.json"
)
NOTEBOOK_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"
REFUSAL_LOG = HOST_DIR / "data" / "logs" / "agent_tool_refusal.jsonl"
PARSER_FAIL_LOG = HOST_DIR / "data" / "logs" / "agent_tool_parser_failures.jsonl"
BOT_COMMANDS = HOST_DIR / "data" / "meta" / "bot_commands.jsonl"
CHAT_DB = HOST_DIR / "data" / "sessions" / "chat_history.sqlite3"
AUDIT_LOG = HOST_DIR / "data" / "logs" / "tool_execution_audit.jsonl"
REPORT_OUT = HOST_DIR / "data" / "logs" / "tool_execution_audit_report.json"

SESSION_CELL30 = "f56ae048-f867-4bfb-94c3-26254b0a4245"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _cell(notebook: dict, index: int) -> dict | None:
    for c in notebook.get("cells") or []:
        if int(c.get("index", -1)) == index:
            return c
    return None


def _bot_commands_for_cells(indices: set[int]) -> list[dict]:
    if not BOT_COMMANDS.exists():
        return []
    out: list[dict] = []
    for line in BOT_COMMANDS.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if NOTEBOOK_URL not in str(o.get("url", "")):
            continue
        ci = o.get("cell_index")
        if ci is not None and int(ci) in indices:
            out.append(o)
    return out


def _chat_assistant_for_goal(goal_substr: str) -> list[dict]:
    if not CHAT_DB.exists():
        return []
    conn = sqlite3.connect(str(CHAT_DB))
    cur = conn.cursor()
    cur.execute(
        "SELECT session_id, role, content FROM messages WHERE lower(content) LIKE ? ORDER BY id",
        (f"%{goal_substr.lower()}%",),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"session_id": r[0], "role": r[1], "content": (r[2] or "")[:800]} for r in rows]


def replay_raw_output(
    *,
    raw_output: str,
    goal: str,
    round_index: int = 0,
    session_id: str | None = None,
    notebook: dict | None = None,
    assistant_final: str | None = None,
    label: str = "",
) -> dict:
    """Simulate pipeline stages without live execution."""
    action_required = is_actionable_notebook_request(goal)
    parse_result = parse_text_tool_batch_result(raw_output, action_required=action_required)
    tool_calls = parse_result.tool_calls
    parsed_tools = _tool_names_from_calls(tool_calls)
    parsed_count = len(tool_calls)

    dispatch_path = None
    dispatcher_received = False
    executor_would_run = False
    use_batch = False
    if parsed_count > 0:
        use_batch = should_use_batch_executor(tool_calls, agentic_active=True)
        if use_batch:
            dispatch_path = "batch_executor"
            dispatcher_received = True
            executor_would_run = True
        else:
            dispatch_path = "sequential_registry"
            dispatcher_received = True
            executor_would_run = True

    # Infer bot command evidence for target cells mentioned in tools
    target_cells: set[int] = set()
    for tc in tool_calls:
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        for key in ("cell_index", "index"):
            if args.get(key) is not None:
                try:
                    target_cells.add(int(args[key]))
                except (TypeError, ValueError):
                    pass
    bot_hits = _bot_commands_for_cells(target_cells) if target_cells else []

    verification_inference: dict | None = None
    notebook_unchanged = None
    if notebook and target_cells:
        cell_states = {}
        for ci in sorted(target_cells):
            c = _cell(notebook, ci)
            if c:
                out = str(c.get("output") or "")
                cell_states[ci] = {
                    "has_keyerror": "KeyError" in out,
                    "output_preview": out[:200],
                    "analyze": analyze_cell_output(out),
                }
        verification_inference = {
            "cells_checked": cell_states,
            "any_still_failing": any(
                v.get("analyze", {}).get("has_error") for v in cell_states.values()
            ),
        }
        notebook_unchanged = verification_inference["any_still_failing"]

    claimed = _assistant_claims_success(assistant_final or raw_output)
    batch = build_batch_record(
        session_id=session_id,
        round_index=round_index,
        goal=goal,
        notebook_url=NOTEBOOK_URL,
        parsed_tools=parsed_tools,
        parsed_tool_count=parsed_count,
        parse_recovery_used=parse_result.recovery_used,
        dispatch_path=dispatch_path,
        dispatcher_received=dispatcher_received,
        executor_called=executor_would_run and bool(bot_hits),
        executor_returned=executor_would_run and bool(bot_hits),
        executor_ok=bool(bot_hits) and all(
            str(h.get("action", "")).startswith(("edit", "insert", "run")) for h in bot_hits
        )
        if bot_hits
        else None,
        verification_received=verification_inference is not None,
        verification_success=(
            not verification_inference["any_still_failing"]
            if verification_inference
            else None
        ),
        verification_summary=verification_inference,
        assistant_final_text=assistant_final,
        assistant_claimed_success=claimed,
        source_hooks=[
            STREAMING_HOOKS["parse"],
            STREAMING_HOOKS["batch_dispatch"] if use_batch else STREAMING_HOOKS["sequential_execute"],
            EXECUTOR_HOOK,
        ],
        extra={
            "replay_label": label,
            "should_use_batch_executor": use_batch,
            "bot_commands_found": len(bot_hits),
            "bot_command_actions": [b.get("action") for b in bot_hits[:5]],
            "parse_errors": list(parse_result.parse_errors),
            "recovery_methods": list(parse_result.recovery_methods),
        },
    )
    batch["failure_case"] = classify_failure_case(batch)
    batch["pipeline_stop_stage"] = infer_pipeline_stop_stage(batch)
    batch["host_decision"] = _host_decision(batch)
    return batch


def _host_decision(batch: dict) -> str:
    case = batch.get("failure_case")
    parsed = int(batch.get("parsed_tool_count") or 0)
    if parsed == 0:
        return "continue_react_or_prose_nudge (streaming.py:~1438-1608)"
    if case == "CASE_A":
        return "tools_parsed_never_dispatched"
    if case == "CASE_B":
        return "dispatch_without_executor"
    if case == "CASE_C":
        return "executor_error"
    if case == "CASE_D":
        return "executor_ok_no_verification"
    if case == "CASE_E":
        return "verification_failed_but_success_claimed (streaming.py:~2096 sanitize may not block)"
    if case == "CASE_F":
        return "no_verification_success_assumed (workflow stop streaming.py:~1870)"
    if batch.get("verification_success") is False and batch.get("assistant_claimed_success"):
        return "notebook_still_failing_success_claimed"
    return "pipeline_ok_or_inconclusive"


def _collect_replay_samples() -> list[dict]:
    samples: list[dict] = []
    seen: set[str] = set()

    def _add(raw: str, goal: str, **kw):
        key = raw[:120]
        if key in seen:
            return
        seen.add(key)
        samples.append({"raw_output": raw, "goal": goal, **kw})

    for row in _load_jsonl(REFUSAL_LOG):
        goal = str(row.get("goal") or "")
        gl = goal.lower()
        if not any(k in gl for k in ("cell 30", "cell 31", "tool execution")):
            continue
        raw = str(row.get("raw_model_response") or row.get("raw_output") or "")
        if not raw:
            continue
        _add(
            raw,
            goal,
            round_index=int(row.get("round") or 0),
            session_id=row.get("session_id"),
            label=f"refusal:{row.get('source')}:{row.get('failure_type')}",
        )

    for row in _load_jsonl(PARSER_FAIL_LOG):
        goal = str(row.get("goal") or "")
        if "cell 30" not in goal.lower() and "cell 31" not in goal.lower():
            continue
        raw = str(row.get("raw_output") or "")
        if raw:
            _add(
                raw,
                goal,
                round_index=int(row.get("round") or 0),
                session_id=row.get("session_id"),
                label="parser_failure",
            )

    # Canonical Cell 30 historical turns (from agent_failure_summary investigation)
    _add(
        "(round 0 prose — no batch tag; 844 completion tokens; host.log: no 'Text tool batch parsed')",
        "fix the error in cell 30 and test until it runs successfully without errors",
        round_index=0,
        session_id=SESSION_CELL30,
        label="cell30_historical_round0_prose",
    )
    return samples


def _historical_cell30_analysis(notebook: dict) -> dict:
    c30 = _cell(notebook, 30)
    c31 = _cell(notebook, 31)
    bot30 = _bot_commands_for_cells({30, 31})
    chats = _chat_assistant_for_goal("cell 30")
    assistant_msgs = [c for c in chats if c["role"] == "assistant"]

    return {
        "notebook_cell_30": {
            "index": 30,
            "still_has_keyerror": "KeyError" in str((c30 or {}).get("output") or ""),
            "input_preview": str((c30 or {}).get("input") or "")[:200],
        },
        "notebook_cell_31": {
            "index": 31,
            "still_has_keyerror": "KeyError" in str((c31 or {}).get("output") or ""),
            "input_preview": str((c31 or {}).get("input") or "")[:200],
        },
        "bot_commands_cells_30_31": len(bot30),
        "bot_command_sample": bot30[-3:] if bot30 else [],
        "assistant_responses_mentioning_cell30": len(assistant_msgs),
        "evidence_no_bot_commands_for_30_31": len(bot30) == 0,
    }


def build_report() -> dict:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    replays: list[dict] = []
    for sample in _collect_replay_samples():
        if sample["raw_output"].startswith("(round 0 prose"):
            replays.append(
                {
                    "replay_label": sample.get("label"),
                    "goal": sample["goal"],
                    "round_index": sample.get("round_index"),
                    "session_id": sample.get("session_id"),
                    "parsed_tool_count": 0,
                    "pipeline_stop_stage": "parse_zero_tools",
                    "failure_case": None,
                    "host_decision": "prose_only_round0_no_dispatch (streaming.py:~1438-1608)",
                    "evidence": {
                        "host_log": "No 'Text tool batch parsed' line; Unknown/invalid tools: []",
                        "source": "agent_failure_summary.md Investigation A",
                    },
                }
            )
            continue
        replays.append(
            replay_raw_output(
                raw_output=sample["raw_output"],
                goal=sample["goal"],
                round_index=int(sample.get("round_index") or 0),
                session_id=sample.get("session_id"),
                notebook=notebook,
                label=str(sample.get("label") or ""),
            )
        )

    case_counts = Counter(r.get("failure_case") for r in replays if r.get("failure_case"))
    stop_counts = Counter(r.get("pipeline_stop_stage") for r in replays)

    historical = _historical_cell30_analysis(notebook)
    live_audit = _load_jsonl(AUDIT_LOG)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_stages": [
            "LLM output",
            "parse_text_tool_batch_result (streaming.py:~1301)",
            "should_use_batch_executor (streaming.py:~1673)",
            "execute_agentic_batch | sequential reg.call (streaming.py:~1679 / ~1880)",
            "verify_workflow_batch / finalize_tool_queue_verification (agentic_batch_executor.py:~1497+)",
            "workflow_needs_llm_followup (agentic_batch_executor.py:99)",
            "final LLM + sanitize_false_success_language (streaming.py:~2096)",
        ],
        "findings": {
            "1_where_pipeline_stops": dict(stop_counts),
            "2_parsed_tools_discarded": [
                r for r in replays
                if int(r.get("parsed_tool_count") or 0) > 0
                and not r.get("dispatcher_received")
            ],
            "3_executor_results_ignored": [
                r for r in replays
                if r.get("executor_called") and r.get("verification_success") is False
                and r.get("assistant_claimed_success")
            ],
            "4_verification_missing": [
                r for r in replays
                if r.get("failure_case") in ("CASE_D", "CASE_F")
                or r.get("pipeline_stop_stage") == "parse_zero_tools"
            ],
            "5_success_hallucinated_by_host": {
                "final_instruction_guard": "streaming.py:~2050-2081 replaces prose with guard message",
                "false_success_sanitize": "streaming.py:~2096 sanitize_false_success_language (agent_goal_verification.py)",
                "workflow_stop_without_goal_verified": "streaming.py:~1870 break when workflow_needs_llm_followup false despite goal_verified false",
                "notebook_evidence": historical,
            },
        },
        "failure_case_counts": dict(case_counts),
        "cell_30_historical": {
            "session_id": SESSION_CELL30,
            "documented_root_cause": (
                "Round 0: parse_zero_tools → no dispatch. "
                "Round 1: 2 tools parsed, batch executor ran, but verification accepted empty/stale "
                "run output as success (wait_for_cell_run / analyze_cell_output); "
                "workflow_needs_llm_followup returned false at streaming.py:~1845; "
                "final summary at streaming.py:~1980 claimed success without goal_verified gate."
            ),
            "line_references": {
                "parse": "streaming.py:1301-1352",
                "batch_execute": "streaming.py:1679-1689",
                "workflow_stop": "agentic_batch_executor.py:99-142 + streaming.py:1845-1878",
                "verification": "agentic_batch_executor.py:1497-1604 finalize_tool_queue_verification",
                "final_response": "streaming.py:1980-2013 + 2096-2101",
                "instruction_guard": "streaming.py:2050-2081",
            },
        },
        "cell_31_keyerror": {
            "notebook": historical["notebook_cell_31"],
            "replay_samples": [
                r for r in replays if "cell 31" in str(r.get("goal", "")).lower()
            ],
        },
        "instruction_only_turns": [
            r for r in replays
            if "instruction" in str(r.get("replay_label", "")).lower()
            or r.get("dispatch_path") == "final_instruction_only_guard"
            or (
                int(r.get("parsed_tool_count") or 0) == 0
                and _assistant_claims_success(str(r.get("assistant_final_text") or ""))
            )
        ],
        "replay_results": replays,
        "live_audit_records_count": len(live_audit),
        "top_failure_pattern": case_counts.most_common(1)[0] if case_counts else ["NONE", 0],
    }
    return report


def main() -> int:
    report = build_report()
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {REPORT_OUT}")
    print(f"Failure cases: {report.get('failure_case_counts')}")
    print(f"Pipeline stops: {report['findings']['1_where_pipeline_stops']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
