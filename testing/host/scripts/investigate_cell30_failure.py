#!/usr/bin/env python3
"""
Read-only reconstruction of the Cell 30 false-success failure (2026-06-14 session).
Generates agent_failure_trace.json and agent_failure_summary.md — no agent fixes.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = HOST_DIR.parents[1]
sys.path.insert(0, str(HOST_DIR))

NOTEBOOK_PATH = HOST_DIR / "data" / "notebooks" / "persistent" / (
    "https___www_kaggle_com_code_codekey_testing_ol_edit.json"
)
HOST_LOG = HOST_DIR / "data" / "logs" / "host.log"
CHAT_DB = HOST_DIR / "data" / "sessions" / "chat_history.sqlite3"
BOT_COMMANDS = HOST_DIR / "data" / "meta" / "bot_commands.jsonl"
TRACE_OUT = HOST_DIR / "data" / "logs" / "agent_failure_trace.json"
SUMMARY_OUT = HOST_DIR / "docs" / "agent_failure_summary.md"

SESSION_ID = "f56ae048-f867-4bfb-94c3-26254b0a4245"
USER_GOAL = "fix the error in cell 30 and test until it runs successfully without errors"
USER_GOAL_ALT = "fix the error in cell 30, and test until it runs successfuly without errors"
NOTEBOOK_URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"

# host.log window for this turn (line numbers from 2026-06-14 investigation)
LOG_LINE_START = 438892
LOG_LINE_END = 439140


def _load_notebook() -> dict:
    return json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))


def _cell(notebook: dict, index: int) -> dict | None:
    for c in notebook.get("cells") or []:
        if int(c.get("index", -1)) == index:
            return c
    return None


def _parse_host_log_window() -> list[str]:
    lines = HOST_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, LOG_LINE_START - 1)
    end = min(len(lines), LOG_LINE_END)
    return lines[start:end]


def _extract_log_events(log_lines: list[str]) -> dict:
    events: list[str] = []
    token_rounds: list[dict] = []
    for ln in log_lines:
        if "Token usage:" in ln:
            m = re.search(
                r"prompt=(\d+), completion=(\d+).*total=(\d+)",
                ln,
            )
            if m:
                token_rounds.append(
                    {
                        "prompt_tokens": int(m.group(1)),
                        "completion_tokens": int(m.group(2)),
                        "total_tokens": int(m.group(3)),
                    }
                )
        for key in (
            "CHAT_REQUEST",
            "Text tool batch parsed",
            "Unknown/invalid tools",
            "Batch run error",
            "Workflow verification failed",
            "Prose-only limit",
            "Turn tokens:",
            "Injected queue error",
        ):
            if key in ln:
                events.append(ln.split("] ", 1)[-1] if "] " in ln else ln)
    return {"events": events, "token_rounds": token_rounds}


def _chat_messages() -> tuple[str, str]:
    conn = sqlite3.connect(str(CHAT_DB))
    cur = conn.cursor()
    cur.execute(
        "SELECT role, content FROM messages WHERE session_id=? AND id >= 453 ORDER BY id LIMIT 4",
        (SESSION_ID,),
    )
    rows = cur.fetchall()
    conn.close()
    user_msg = assistant_msg = ""
    for role, content in rows:
        if role == "user" and "cell 30" in (content or "").lower():
            user_msg = content or ""
        elif role == "assistant" and "cell" in (content or "").lower() and "fixed" in (content or "").lower():
            assistant_msg = content or ""
    return user_msg, assistant_msg


def _bot_commands_for_session() -> list[dict]:
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
        if ci is not None and int(ci) >= 28:
            out.append(o)
    return out


def _build_state_block(notebook: dict, cell_index: int) -> dict:
    from notebook_context import pack_context, build_dependency_graph, _cells_from_data
    from runtime_state import format_runtime_state_block, empty_runtime_state
    from agent_state import format_agent_state_block, empty_agent_state

    cells = _cells_from_data(notebook)
    tracker, _, _ = build_dependency_graph(cells)
    pack = pack_context(
        mode="agentic",
        url=NOTEBOOK_URL,
        prompt=USER_GOAL,
        cell_index=cell_index,
    )
    context_text = getattr(pack, "text", None) or str(pack)
    agent_state = empty_agent_state(goal=USER_GOAL)
    runtime = empty_runtime_state(notebook_key=NOTEBOOK_URL)
    cell = _cell(notebook, cell_index)
    err_out = str((cell or {}).get("output") or "")
    last_error = {
        "cell_index": cell_index,
        "error_type": "KeyError",
        "summary": "KeyError: 'price'" if "KeyError" in err_out else str(err_out)[:500],
    }
    runtime["last_error"] = last_error
    runtime["last_failed_cell"] = cell_index
    dep_lines = []
    try:
        deps = tracker.get_dependencies(cell_index) if tracker else []
        dep_lines = [f"cell {cell_index} depends on: {deps}"]
    except Exception:
        dep_lines = ["(dependency engine unavailable)"]
    return {
        "GOAL": USER_GOAL,
        "PLAN": format_agent_state_block(agent_state) or "(none — planner did not run for this turn)",
        "NOTEBOOK_STATE": context_text[:4000],
        "DEPENDENCY_SUMMARY": "\n".join(dep_lines)[:2000],
        "RUNTIME_STATE": format_runtime_state_block(runtime),
        "LAST_ERROR": last_error,
    }


def _action_required() -> bool:
    from agentic_action_guard import is_actionable_notebook_request

    return is_actionable_notebook_request(USER_GOAL)


def _infer_rounds(log: dict, assistant_response: str) -> list[dict]:
    tokens = log["token_rounds"]
    # Round 0: first LLM call — no tool batch
    r0 = {
        "round": 0,
        "user_goal": USER_GOAL,
        "raw_prompt_tokens": tokens[0]["prompt_tokens"] if tokens else 5325,
        "raw_model_response": (
            "(not persisted — host.log shows completion=844 tokens, "
            "Unknown/invalid tools: [], no 'Text tool batch parsed' line)"
        ),
        "tool_batch_found": False,
        "parsed_tools": [],
        "tools_executed": [],
        "verification_result": None,
        "continue_react": True,
        "continue_reason": "prose_only_or_unparsed_batch — action nudge injected",
        "stop_reason": None,
        "goal_verified": False,
        "assistant_response": None,
        "host_log_notes": [
            "Agentic mode: tool-first path (skipped prose stream)",
            "Unknown/invalid tools: []",
        ],
    }
    # Round 1: second LLM — 2 tools parsed
    r1 = {
        "round": 1,
        "user_goal": USER_GOAL,
        "raw_prompt_tokens": tokens[1]["prompt_tokens"] if len(tokens) > 1 else 5696,
        "raw_model_response": (
            "(not persisted — host.log: Text tool batch parsed: 2 tool(s), completion=655)"
        ),
        "tool_batch_found": True,
        "parsed_tools": [
            {"tool": "edit_cell_by_index", "cell_index": 30, "inferred": True},
            {"tool": "run_cell", "cell_index": 30, "inferred": True},
        ],
        "parsed_tool_count": 2,
        "tools_executed": [
            {
                "tool": "edit_cell_by_index",
                "cell_index": 30,
                "executed": False,
                "evidence": "cell 30 source unchanged in persistent snapshot; no bot_commands entry for cell_index 30",
            },
            {
                "tool": "run_cell",
                "cell_index": 30,
                "executed": True,
                "evidence": "PROMPT_SIGNAL cell=30 at 17:21:14–17:21:15; execution_order=11; KeyError remains",
            },
        ],
        "executed_tool_count": 1,
        "verification_result": {
            "inferred_from_logs": True,
            "batch_run_error_logged": False,
            "workflow_verification_failed_logged": False,
            "note": (
                "No 'Batch run error cell 30' or 'Workflow verification failed' in host.log. "
                "Likely wait_for_cell_run returned on execution_order change before error output "
                "was scraped — analyze_cell_output('') treats empty output as run_succeeded=True."
            ),
            "workflow_needs_llm_followup": False,
            "workflow_followup_reason": "queue_complete_no_pending_go_to_final_summary",
        },
        "continue_react": False,
        "continue_reason": None,
        "stop_reason": "queue_complete_no_pending_go_to_final_summary",
        "goal_verified": False,
        "assistant_response": None,
    }
    # Final summary LLM
    r2 = {
        "round": 2,
        "user_goal": USER_GOAL,
        "raw_prompt_tokens": tokens[2]["prompt_tokens"] if len(tokens) > 2 else 5985,
        "raw_model_response": assistant_response or (
            "The error in cell 30 has been fixed. The cell now loads the dataset, creates "
            "df_clean, checks that the price column exists, and then plots the price "
            "distribution without raising a KeyError. The cell runs successfully."
        ),
        "tool_batch_found": False,
        "parsed_tools": [],
        "tools_executed": [],
        "verification_result": {
            "response_allowed": True,
            "goal_verified": False,
            "sanitize_false_success_applied": False,
            "note": "last_batch_verification likely marked verified/complete before KeyError output landed",
        },
        "continue_react": False,
        "continue_reason": None,
        "stop_reason": "final_summary_after_react_stop",
        "goal_verified": False,
        "assistant_response": assistant_response,
    }
    return [r0, r1, r2]


def _build_summary(trace: dict, notebook: dict, cell30: dict) -> str:
    inv = trace["investigations"]
    root = trace["root_cause"]
    return f"""# Cell 30 Agent Failure — Investigation Summary

**Case:** `{NOTEBOOK_PATH.name}` · Cell 30 · `KeyError: 'price'` on `df_clean['price']`  
**Session:** `{SESSION_ID}` · **Timestamp:** 2026-06-14 ~17:19–17:21 UTC  
**User prompt:** `{USER_GOAL}`

## Executive answers

| Question | Answer |
|----------|--------|
| Did the model emit tools? | **Partially.** Round 0: **no** valid batch (844-token prose/unparsed). Round 1: **yes** — 2 tools parsed. |
| Did the parser fail? | **Round 0 only.** No `Text tool batch parsed` line; `Unknown/invalid tools: []`. Round 1 parsed 2 tools successfully. |
| Did execution fail? | **Effective yes.** Cell 30 source was **not** edited. `run_cell(30)` ran **after** the loop declared completion (~17:21:14 vs turn end ~17:21:01). Output still `KeyError: 'price'`. |
| Did verification fail? | **Should have — but did not trigger recovery.** No `Batch run error cell 30` or `Workflow verification failed` in host.log for this turn. |
| Why did the loop stop? | `workflow_needs_llm_followup()` → **false**; `workflow_followup_reason()` → **`queue_complete_no_pending_go_to_final_summary`** (inferred from absence of continue logs + final summary at 17:21:00). |
| Why was success response allowed? | Final prose round ran with **no goal_verified gate** on empty/stale verification; `sanitize_false_success_language` had nothing to block (batch marked complete). |
| Single root cause? | **Verification + loop termination (stages 4–6):** run completion was accepted before cell 30 error output was observable, so the ReAct loop exited and the model emitted a false success summary. Contributing: round 0 prose-only; no upstream `notebook_get_cell` diagnosis; no durable edit to cell 30. |

## Investigation A — Tool batch trace

```json
{json.dumps(inv["A"], indent=2)}
```

## Investigation B — Upstream diagnosis

```json
{json.dumps(inv["B"], indent=2)}
```

**Data context:** Cell 23 loads CSV **without** `sep='|'`, so columns are pipe-concatenated and `df_clean` never gets a real `price` column. A correct fix requires upstream inspection (cells 23–25), not only cell 30.

## Investigation C — False success

```json
{json.dumps(inv["C"], indent=2)}
```

Persisted cell 30 output after the turn:

```
{str(cell30.get("output", ""))[:400]}…
```

## Investigation D — Prompt state block (reconstructed at failure time)

```json
{json.dumps({k: (v[:800] + "…" if isinstance(v, str) and len(v) > 800 else v) for k, v in inv["D"].items()}, indent=2)}
```

## Investigation E — Tool requirement enforcement

```json
{json.dumps(inv["E"], indent=2)}
```

## Stage failure map

| Stage | Failed? | Evidence |
|-------|---------|----------|
| 1. LLM generation | **Partial** | Round 0 prose-only; round 1 emitted tools |
| 2. Tool parsing | **Round 0 only** | No batch parsed first call |
| 3. Tool execution | **Yes** | No edit persisted; run raced loop exit |
| 4. Verification | **Yes** | KeyError not surfaced; no batch error log |
| 5. Loop continuation | **Yes** | Stopped despite unresolved goal |
| 6. Final response | **Yes** | False success text saved to chat history |

## Root cause (single sentence)

{root["statement"]}

## Recommended fix direction (diagnosis only — not implemented)

1. Treat `execution_order` change with **empty output** as **pending**, not success (`wait_for_cell_run` / `analyze_cell_output`).
2. Block final summary when `goal_verified` is false for fix-and-test prompts.
3. Require `notebook_get_cell` on failing cell + `df_clean` origin before edit when error is `KeyError` on column access.

---
*Generated by `scripts/investigate_cell30_failure.py` at {trace["generated_at"]}*
"""


def main() -> int:
    notebook = _load_notebook()
    cell30 = _cell(notebook, 30) or {}
    log_lines = _parse_host_log_window()
    log_data = _extract_log_events(log_lines)
    user_msg, assistant_msg = _chat_messages()
    state_block = _build_state_block(notebook, 30)
    action_required = _action_required()
    bot_high = _bot_commands_for_session()
    rounds = _infer_rounds(log_data, assistant_msg)

    investigations = {
        "A": {
            "A1_tool_batch_found_round0": False,
            "A1_tool_batch_found_round1": True,
            "A2_tools_round1_inferred": rounds[1]["parsed_tools"],
            "A3_parsed_tool_count_round1": 2,
            "A4_executed_tool_count": 1,
            "A5_verification_payload": rounds[1]["verification_result"],
            "A6_loop_termination": {
                "workflow_needs_llm_followup": False,
                "workflow_followup_reason": "queue_complete_no_pending_go_to_final_summary",
            },
        },
        "B": {
            "upstream_inspection_performed": False,
            "notebook_get_cell_issued": False,
            "evidence": "No bot_commands with notebook_get_cell; no reads of cells 23–25 in host.log for this turn",
        },
        "C": {
            "goal_verified": False,
            "execution_verified": False,
            "edit_verified": False,
            "response_claimed_success": "fixed" in assistant_msg.lower() and "success" in assistant_msg.lower(),
            "false_success": True,
        },
        "D": state_block,
        "E": {
            "action_required": action_required,
            "required_tools_executed": False,
            "note": "Fix+test prompt requires edit+run+verify; edit not persisted, goal not verified",
        },
    }

    trace = {
        "case_id": "cell30_keyerror_testing_ol",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notebook_snapshot": str(NOTEBOOK_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "session_id": SESSION_ID,
        "user_goal": user_msg or USER_GOAL_ALT,
        "assistant_response_saved": assistant_msg,
        "cell_30_before_after": {
            "source_unchanged": True,
            "output_error": "KeyError: 'price'" in str(cell30.get("output", "")),
            "execution_order_after_turn": cell30.get("execution_order"),
        },
        "host_log_line_range": [LOG_LINE_START, LOG_LINE_END],
        "host_log_events": log_data["events"],
        "bot_commands_high_cells": bot_high,
        "rounds": rounds,
        "investigations": investigations,
        "root_cause": {
            "primary_stage": "verification_and_loop_termination",
            "secondary_stages": ["llm_generation_round0", "tool_execution_no_edit"],
            "statement": (
                "The batch executor / wait_for_cell_run path marked the run queue complete before cell 30's "
                "KeyError output was available (execution_order changed with empty or stale output → "
                "run_succeeded=True), so workflow_needs_llm_followup returned false, the ReAct loop stopped, "
                "and the final LLM turn emitted an unverified success message while the notebook still contained "
                "the original failing code."
            ),
        },
    }

    TRACE_OUT.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_OUT.parent.mkdir(parents=True, exist_ok=True)
    TRACE_OUT.write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
    SUMMARY_OUT.write_text(_build_summary(trace, notebook, cell30), encoding="utf-8")

    print(f"Wrote {TRACE_OUT}")
    print(f"Wrote {SUMMARY_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
