#!/usr/bin/env python3
"""
Phase 3–6: Replay action-required prompts against GPT-OSS (Cerebras), measure refusals,
run recovery strategies A/B/C, write tool_refusal_report.md.

Does NOT modify planner, semantic index, dependency graph, runtime state, or verification.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1]
REPO = HOST_DIR.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.agent_tool_refusal import (  # noqa: E402
    REFUSAL_LOG,
    append_tool_refusal_record,
    build_tool_refusal_record,
    classify_tool_refusal_failure,
    measure_prompt_context_sizes,
)
from testing.host.agentic_action_guard import is_actionable_notebook_request  # noqa: E402
from testing.host.agentic_mode import set_dashboard_agentic_enabled  # noqa: E402
from testing.host.agentic_text_tools import parse_text_tool_batch_result  # noqa: E402
from testing.host.agent_state import empty_agent_state, inject_agent_state_message  # noqa: E402
from testing.host.config import (  # noqa: E402
    CEREBRAS_API_KEY,
    LLM_MODEL,
    LLM_PROVIDER,
    TEMPERATURE,
    TOP_P,
    _LLM_CLIENT,
)
from testing.host.notebook_context import pack_context  # noqa: E402
from testing.host.prompt_engineering import build_chat_messages  # noqa: E402
from testing.host.streaming import _completion_extra_kwargs, _final_text_from_response  # noqa: E402
from testing.host.token_usage import extract_usage_from_response  # noqa: E402

URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"
REPORT_PATH = HOST_DIR / "docs" / "tool_refusal_report.md"
RESULTS_PATH = HOST_DIR / "data" / "logs" / "tool_refusal_study_results.json"

RECOVERY_A = "Retry the request."
RECOVERY_B = "You MUST respond with exactly one <agent_tool_batch>."
RECOVERY_C = "No prose. Tool calls only."

PROMPTS: list[str] = [
    "fix the error in cell 30 and test until it runs successfully without errors",
    "fix the error in cell 29",
    "fix last cell, it has error",
    "run cell 10",
    "run cell 23 to 25",
    "run last three cells of this notebook",
    "insert visualization code below cell 23 and run it",
    "make the relevant charts to visualize the dataset",
    "input visualization code into cell 28 and run the cell",
    "create a dataframe from the csv and show head",
    "load the dataset and print shape",
    "train logistic regression on price column",
    "train a model to predict high_price",
    "generate submission.csv",
    "debug the KeyError in cell 30",
    "edit cell 5 to fix the import error and run it",
    "insert a new code cell below cell 24 with sep='|' read_csv and run",
    "add preprocessing code after cell 23",
    "remove the broken column check in cell 28 and run",
    "clean df_clean and ensure price column exists",
    "run cell 30 and verify output has no errors",
    "execute cells 26 through 30",
    "fix cell 25 price column not found error",
    "put the plotting code in cell 30 and test it",
    "implement data loading with pipe separator in cell 23",
    "write code to encode categoricals in cell 24",
    "show price distribution histogram in cell 30",
    "create df_clean with proper columns and plot price",
    "run the model training cell and fix any errors",
    "insert markdown explaining the fix below cell 30",
    "delete cell 31 if empty",
    "click cell 30 and run it after fixing KeyError",
    "add sep='|' to read_csv in cell 23 and rerun downstream cells",
    "verify cell 30 runs without KeyError",
    "fix and run cell 30 until success",
    "run cell 27 and fix if it fails",
    "train model and save submission to /kaggle/working/submission.csv",
    "load /kaggle/input/datasets/codekey/zameen-com2026-16-5/zameen_master_dataset.csv and explore",
    "fix notebook so df has a price column",
    "edit cell 30 to use df instead of df_clean and run",
    "run cell 22",
    "insert code below cell 29 to diagnose columns in df_clean",
    "execute cell 30 after fixing upstream data load",
    "create visualization of price by city",
    "generate charts for the housing dataset",
    "debug notebook starting from cell 23",
    "fix all cells that reference df_clean price",
    "run cells 23 24 25 then fix cell 30",
    "implement full pipeline: load, clean, train, submit",
    "test cell 30 until it passes",
]


def _build_messages(prompt: str, *, recovery_suffix: str | None = None) -> tuple[list[dict], dict]:
    pack = pack_context(mode="agentic", url=URL, prompt=prompt, cell_index=30)
    context = getattr(pack, "text", None) or str(pack)
    tail = recovery_suffix or ""
    messages = build_chat_messages(
        mode="agentic",
        user_prompt=prompt,
        history=[],
        context=context,
        notebook_url=URL,
        include_tools=True,
        text_tool_calls=True,
        turn_tail=tail,
    )
    agent_state = empty_agent_state(goal=prompt)
    agent_state["last_error"] = {
        "cell_index": 30,
        "error_summary": "KeyError: 'price'",
        "required_action": "fix upstream load or plot column",
    }
    messages = inject_agent_state_message(messages, agent_state)
    return messages, agent_state


def _call_llm(messages: list[dict]) -> tuple[str, dict]:
    try:
        from testing.host.context_budget import messages_for_api
    except Exception:
        from context_budget import messages_for_api
    extra = _completion_extra_kwargs()
    resp = _LLM_CLIENT.chat.completions.create(
        messages=messages_for_api(messages),
        model=LLM_MODEL,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        **extra,
    )
    content = _final_text_from_response(resp)
    usage = extract_usage_from_response(resp)
    return content, usage


def _evaluate_prompt(
    prompt: str,
    *,
    recovery_suffix: str | None = None,
    prompt_id: str = "",
) -> dict:
    messages, agent_state = _build_messages(prompt, recovery_suffix=recovery_suffix)
    raw, usage = _call_llm(messages)
    parse_result = parse_text_tool_batch_result(raw)
    parsed_count = len(parse_result.tool_calls)
    batch_found = bool(parse_result.batch_count) or "<agent_tool_batch>" in raw.lower()
    failure_type = classify_tool_refusal_failure(raw_model_response=raw, parse_result=parse_result)
    action_required = is_actionable_notebook_request(prompt)
    sizes = measure_prompt_context_sizes(messages, agent_state=agent_state)
    row = {
        "prompt_id": prompt_id,
        "goal": prompt,
        "action_required": action_required,
        "recovery_suffix": recovery_suffix,
        "tool_batch_found": batch_found,
        "parsed_tool_count": parsed_count,
        "failure_type": failure_type if parsed_count == 0 else "OK",
        "prompt_tokens": usage.get("prompt_tokens"),
        "response_tokens": usage.get("completion_tokens"),
        "raw_model_response_preview": raw[:1200],
        "parse_feedback": parse_result.to_feedback_dict(),
        "prompt_inspection": sizes,
        "success": parsed_count > 0,
    }
    if action_required and parsed_count == 0:
        record = build_tool_refusal_record(
            goal=prompt,
            round_idx=0,
            raw_model_response=raw,
            tool_batch_found=batch_found,
            parsed_tool_count=0,
            prompt_tokens=usage.get("prompt_tokens"),
            response_tokens=usage.get("completion_tokens"),
            parse_result=parse_result,
            messages=messages,
            agent_state=agent_state,
            extra={"source": "tool_refusal_study", "prompt_id": prompt_id, "recovery_suffix": recovery_suffix},
        )
        append_tool_refusal_record(record)
    return row


def _correlation(refusals: list[dict], key: str) -> dict:
    if len(refusals) < 1:
        return {"note": "insufficient refusal samples", "n": len(refusals)}
    xs = [
        float((r.get("prompt_inspection") or {}).get(key) or 0)
        for r in refusals
        if isinstance(r.get("prompt_inspection"), dict)
    ]
    if not xs:
        return {"note": f"no {key} data", "n": len(refusals)}
    return {
        "refusal_count": len(refusals),
        "mean": round(statistics.mean(xs), 1),
        "median": round(statistics.median(xs), 1),
        "max": max(xs),
    }


def _write_report(payload: dict) -> None:
    m = payload["metrics"]
    rec = payload["recovery"]
    lines = [
        "# Tool Refusal Study Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Model:** {payload['model']} ({payload['provider']})",
        f"**Notebook:** `{URL}`",
        "",
        "## Phase 3 — Frequency (50 action-required prompts)",
        "",
        f"| Metric | Count |",
        f"|--------|------:|",
        f"| total_prompts | {m['total_prompts']} |",
        f"| tool_batches (parsed_tool_count > 0) | {m['tool_batches']} |",
        f"| tool_refusals (parsed_tool_count == 0) | {m['tool_refusals']} |",
        f"| parse_failures (MALFORMED/EMPTY/UNKNOWN) | {m['parse_failures']} |",
        f"| prose_only | {m['by_type'].get('PROSE_ONLY', 0)} |",
        f"| tool_refusal (explicit) | {m['by_type'].get('TOOL_REFUSAL', 0)} |",
        "",
        f"**Tool batch rate:** {m['tool_batch_rate']:.1%}",
        f"**Refusal rate:** {m['refusal_rate']:.1%}",
        "",
        "## Phase 2 — Failure type breakdown",
        "",
        "```json",
        json.dumps(m["by_type"], indent=2),
        "```",
        "",
        "## Phase 4 — Prompt size vs refusals",
        "",
    ]
    for label, stats in payload.get("size_stats", {}).items():
        lines.append(f"- **{label}** (refusals): {json.dumps(stats)}")
    lines.extend([
        "",
        "## Phase 5 — Recovery experiment (failed prompts only)",
        "",
        "| Strategy | Recovery message | Attempts | Successes | Rate |",
        "|----------|------------------|----------|-----------|------|",
    ])
    for key, label in [("A", RECOVERY_A), ("B", RECOVERY_B), ("C", RECOVERY_C)]:
        r = rec[key]
        rate = r["successes"] / r["attempts"] if r["attempts"] else 0.0
        lines.append(f"| {key} | {label[:50]}… | {r['attempts']} | {r['successes']} | {rate:.1%} |")
    best = max(rec.items(), key=lambda kv: (kv[1]["successes"] / kv[1]["attempts"] if kv[1]["attempts"] else 0))
    lines.extend([
        "",
        f"**Best recovery strategy:** {best[0]} ({best[1]['successes']}/{best[1]['attempts']})",
        "",
        "## Phase 6 — Answers",
        "",
        f"1. **Did GPT-OSS refuse tool mode?** {'Yes — ' + str(m['by_type'].get('TOOL_REFUSAL', 0)) + ' explicit refusals; ' + str(m['tool_refusals']) + ' total zero-tool responses.' if m['tool_refusals'] else 'Mostly no explicit refusals; failures are primarily PROSE_ONLY / parse issues.'}",
        f"2. **How often?** {m['refusal_rate']:.1%} of prompts ({m['tool_refusals']}/{m['total_prompts']}) returned zero parsed tools.",
        f"3. **Correlated with prompt size?** See size_stats above. "
        f"Compare mean prompt_est_tokens: refusals={payload.get('refusal_mean_tokens', 'n/a')} vs successes={payload.get('success_mean_tokens', 'n/a')}.",
        f"4. **Best recovery?** Strategy **{best[0]}**.",
        "5. **Model vs host?** "
        + (
            "Primarily **model behavior** (prose/instruction-style replies without `<agent_tool_batch>`). "
            "Host guard correctly blocks false success; logging in `agent_tool_refusal.jsonl` captures raw responses."
            if m["by_type"].get("PROSE_ONLY", 0) >= m["tool_refusals"] // 2
            else "Mixed: check MALFORMED_BATCH / UNKNOWN_TOOL_ONLY counts for parser vs model issues."
        ),
        "",
        "## Diagnostics",
        "",
        f"- Refusal log: `{REFUSAL_LOG.relative_to(REPO).as_posix()}`",
        f"- Full results: `{RESULTS_PATH.relative_to(REPO).as_posix()}`",
        "",
        "## Sample refusals (first 3)",
        "",
    ])
    for sample in payload.get("refusal_samples", [])[:3]:
        lines.append(f"### {sample.get('prompt_id')}")
        lines.append(f"- failure_type: `{sample.get('failure_type')}`")
        lines.append(f"- prompt_est_tokens: {sample.get('prompt_inspection', {}).get('prompt_est_tokens')}")
        lines.append(f"- preview: {sample.get('raw_model_response_preview', '')[:400]}…")
        lines.append("")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not CEREBRAS_API_KEY:
        print("ERROR: CEREBRAS_API_KEY not set — cannot run live replay.")
        return 1

    set_dashboard_agentic_enabled(True)
    prompts = PROMPTS[:50]
    while len(prompts) < 50:
        prompts.append(PROMPTS[len(prompts) % len(PROMPTS)])

    results: list[dict] = []
    print(f"Running {len(prompts)} prompts against {LLM_MODEL}...")
    for i, prompt in enumerate(prompts):
        pid = f"p{i + 1:02d}"
        print(f"  [{i + 1}/{len(prompts)}] {prompt[:60]}...")
        try:
            row = _evaluate_prompt(prompt, prompt_id=pid)
        except Exception as exc:
            row = {
                "prompt_id": pid,
                "goal": prompt,
                "error": str(exc),
                "success": False,
                "parsed_tool_count": 0,
                "failure_type": "API_ERROR",
                "prompt_inspection": {},
            }
        results.append(row)
        time.sleep(0.5)

    tool_batches = sum(1 for r in results if int(r.get("parsed_tool_count") or 0) > 0)
    refusals = [r for r in results if int(r.get("parsed_tool_count") or 0) == 0]
    parse_fail_types = {"MALFORMED_BATCH", "EMPTY_BATCH", "UNKNOWN_TOOL_ONLY"}
    parse_failures = sum(1 for r in refusals if r.get("failure_type") in parse_fail_types)
    by_type: dict[str, int] = {}
    for r in refusals:
        ft = str(r.get("failure_type") or "UNKNOWN")
        by_type[ft] = by_type.get(ft, 0) + 1

    recovery = {
        "A": {"suffix": RECOVERY_A, "attempts": 0, "successes": 0, "results": []},
        "B": {"suffix": RECOVERY_B, "attempts": 0, "successes": 0, "results": []},
        "C": {"suffix": RECOVERY_C, "attempts": 0, "successes": 0, "results": []},
    }
    failed = [r for r in results if not r.get("success") and not r.get("error")]
    # Cap recovery sample to limit API cost (first 10 hard failures)
    failed = failed[:10]
    print(f"\nRecovery experiment on {len(failed)} failed prompts...")
    for r in failed:
        goal = r.get("goal") or ""
        for key, suffix in [("A", RECOVERY_A), ("B", RECOVERY_B), ("C", RECOVERY_C)]:
            recovery[key]["attempts"] += 1
            try:
                rr = _evaluate_prompt(
                    goal,
                    recovery_suffix=suffix,
                    prompt_id=f"{r.get('prompt_id')}_{key}",
                )
            except Exception as exc:
                rr = {"success": False, "error": str(exc)}
            recovery[key]["results"].append(rr)
            if rr.get("success"):
                recovery[key]["successes"] += 1
            time.sleep(0.5)

    success_tokens = [
        float(r["prompt_inspection"]["prompt_est_tokens"])
        for r in results
        if r.get("success") and isinstance(r.get("prompt_inspection"), dict)
    ]
    refusal_tokens = [
        float(r["prompt_inspection"]["prompt_est_tokens"])
        for r in refusals
        if isinstance(r.get("prompt_inspection"), dict)
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": LLM_MODEL,
        "provider": LLM_PROVIDER,
        "metrics": {
            "total_prompts": len(results),
            "tool_batches": tool_batches,
            "tool_refusals": len(refusals),
            "parse_failures": parse_failures,
            "tool_batch_rate": tool_batches / len(results) if results else 0,
            "refusal_rate": len(refusals) / len(results) if results else 0,
            "by_type": by_type,
        },
        "recovery": recovery,
        "results": results,
        "size_stats": {
            "prompt_est_tokens": _correlation(refusals, "prompt_est_tokens"),
            "state_block_chars": _correlation(refusals, "state_block_chars"),
            "semantic_index_chars": _correlation(refusals, "semantic_index_chars"),
            "dependency_graph_chars": _correlation(refusals, "dependency_graph_chars"),
            "runtime_state_chars": _correlation(refusals, "runtime_state_chars"),
        },
        "refusal_mean_tokens": round(statistics.mean(refusal_tokens), 1) if refusal_tokens else None,
        "success_mean_tokens": round(statistics.mean(success_tokens), 1) if success_tokens else None,
        "refusal_samples": refusals[:5],
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_report(payload)
    print(f"\nWrote {RESULTS_PATH}")
    print(f"Wrote {REPORT_PATH}")
    print(
        f"Summary: {tool_batches}/{len(results)} tool batches, "
        f"{len(refusals)} refusals, parse_failures={parse_failures}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
