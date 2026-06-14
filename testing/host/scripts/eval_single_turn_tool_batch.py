#!/usr/bin/env python3
"""Eval: does the LLM emit all required tool calls in a single response?

No browser/host execution — LLM round-trip only.

Usage:
  python testing/host/scripts/eval_single_turn_tool_batch.py
  python testing/host/scripts/eval_single_turn_tool_batch.py --min-tools 6
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.agentic_mode import set_dashboard_agentic_enabled  # noqa: E402
from testing.host.config import LLM_MODEL, LLM_PROVIDER, TEMPERATURE, TOP_P, _LLM_CLIENT  # noqa: E402
from testing.host.prompt_engineering import agentic_runtime_enabled, build_chat_messages  # noqa: E402
from testing.host.streaming import _completion_extra_kwargs, _parallel_tool_calls_flag  # noqa: E402
from testing.host.tool_registry import build_cerebras_tools  # noqa: E402

URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"

# Requires 6+ distinct tool types in one turn when indices are known.
COMPLEX_PROMPT = (
    "Cell indices are known. In ONE tool response emit ALL of these — do not split across rounds: "
    "insert_cell below cell 2, edit_cell_by_index on cell 3 with print('batch_eval_1'), "
    "insert_cell below cell 3, edit_cell_by_index on cell 4 with print('batch_eval_2'), "
    "run_cell on cell 3, run_cell on cell 4. No notebook_list_cells. Browser tools only."
)


@dataclass
class EvalReport:
    provider: str
    model: str
    parallel_tool_calls: bool
    prompt: str
    round1_tool_count: int = 0
    round1_unique_tools: list[str] = field(default_factory=list)
    round1_tool_calls: list[dict] = field(default_factory=list)
    round2_tool_count: int = 0
    assistant_text_round1: str = ""
    min_tools_required: int = 6
    min_unique_required: int = 5
    pass_single_turn: bool = False
    pass_tool_diversity: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "parallel_tool_calls": self.parallel_tool_calls,
            "prompt": self.prompt,
            "round1_tool_count": self.round1_tool_count,
            "round1_unique_tools": self.round1_unique_tools,
            "round2_tool_count": self.round2_tool_count,
            "min_tools_required": self.min_tools_required,
            "min_unique_required": self.min_unique_required,
            "pass_single_turn": self.pass_single_turn,
            "pass_tool_diversity": self.pass_tool_diversity,
            "pass_overall": self.pass_single_turn and self.pass_tool_diversity,
            "notes": self.notes,
            "round1_tool_calls_preview": self.round1_tool_calls[:12],
        }


def _parse_tool_calls(msg: dict) -> list[dict]:
    return msg.get("tool_calls") or []


def _tool_names(tool_calls: list[dict]) -> list[str]:
    out: list[str] = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if name:
            out.append(name)
    return out


def _tool_args_preview(tool_calls: list[dict]) -> list[dict]:
    preview: list[dict] = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            args = {"_raw": str(raw)[:200]}
        preview.append({"id": tc.get("id"), "name": fn.get("name"), "args": args})
    return preview


def run_eval(*, min_tools: int, min_unique: int, prompt: str) -> EvalReport:
    if _LLM_CLIENT is None:
        raise RuntimeError("LLM client not configured — check .env / API keys")

    set_dashboard_agentic_enabled(True)
    mode = "agentic"
    if not agentic_runtime_enabled(mode):
        raise RuntimeError("Agentic runtime not enabled")

    report = EvalReport(
        provider=LLM_PROVIDER,
        model=LLM_MODEL,
        parallel_tool_calls=_parallel_tool_calls_flag(agentic=True),
        prompt=prompt,
        min_tools_required=min_tools,
        min_unique_required=min_unique,
    )

    messages = build_chat_messages(
        mode=mode,
        user_prompt=prompt,
        history=[],
        context="Notebook has code cells 1-25. Cell 2 and 3 are code cells.",
        notebook_url=URL,
        include_tools=True,
        turn_tail=(
            "Respond with ONE assistant message containing ALL tool_calls required "
            "(every insert, edit, run_cell, etc.). Do not use one tool per round."
        ),
    )
    tools = build_cerebras_tools(include_browser=True)
    extra = _completion_extra_kwargs()

    resp = _LLM_CLIENT.chat.completions.create(
        messages=messages,
        model=LLM_MODEL,
        tools=tools,
        parallel_tool_calls=report.parallel_tool_calls,
        tool_choice="required",
        temperature=TEMPERATURE,
        top_p=TOP_P,
        **extra,
    )
    dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
    choice = (dumped.get("choices") or [{}])[0]
    assistant_msg = choice.get("message") or {}
    report.assistant_text_round1 = str(assistant_msg.get("content") or "").strip()
    round1 = _parse_tool_calls(assistant_msg)
    report.round1_tool_count = len(round1)
    report.round1_unique_tools = sorted(set(_tool_names(round1)))
    report.round1_tool_calls = _tool_args_preview(round1)

    report.pass_single_turn = report.round1_tool_count >= min_tools
    report.pass_tool_diversity = len(report.round1_unique_tools) >= min_unique

    if report.round1_tool_count < min_tools:
        report.notes.append(
            f"Round 1 emitted {report.round1_tool_count} tools; need >= {min_tools} in one response."
        )
    if len(report.round1_unique_tools) < min_unique:
        report.notes.append(
            f"Only {len(report.round1_unique_tools)} unique tools: {report.round1_unique_tools}"
        )
    if not report.parallel_tool_calls:
        report.notes.append(
            "Provider has parallel_tool_calls=false (e.g. Cerebras) — model may cap at 1 call/round."
        )
    run_calls = [n for n in report.round1_unique_tools if n == "run_cell"]
    if report.round1_tool_count and report.round1_tool_count < 2 and "run_cell" in report.round1_unique_tools:
        report.notes.append("Run tools likely split across rounds — host queue cannot drain without enrich.")

    # Optional round 2 probe (did model stop after partial batch?)
    if round1:
        tool_messages = list(messages)
        tool_messages.append({
            "role": "assistant",
            "content": report.assistant_text_round1,
            "tool_calls": round1,
        })
        for tc in round1:
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or "probe",
                "content": json.dumps({"ok": True, "probe": True}),
            })
        resp2 = _LLM_CLIENT.chat.completions.create(
            messages=tool_messages,
            model=LLM_MODEL,
            tools=tools,
            parallel_tool_calls=report.parallel_tool_calls,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            **extra,
        )
        dumped2 = resp2.model_dump() if hasattr(resp2, "model_dump") else {}
        msg2 = ((dumped2.get("choices") or [{}])[0].get("message") or {})
        report.round2_tool_count = len(_parse_tool_calls(msg2))
        if report.round2_tool_count > 0 and report.round1_tool_count < min_tools:
            report.notes.append(
                f"Model sent {report.round2_tool_count} more tool(s) in round 2 — not single-turn batching."
            )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval single-turn multi-tool LLM batch")
    parser.add_argument("--min-tools", type=int, default=6, help="Min tool calls required in round 1")
    parser.add_argument("--min-unique", type=int, default=4, help="Min distinct tool names in round 1")
    parser.add_argument("--prompt", default=COMPLEX_PROMPT)
    args = parser.parse_args()

    print("=== Single-turn tool batch eval ===")
    print(f"provider={LLM_PROVIDER} model={LLM_MODEL}")

    try:
        report = run_eval(min_tools=args.min_tools, min_unique=args.min_unique, prompt=args.prompt)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2

    data = report.to_dict()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("\n--- Summary ---")
    print(f"Round 1 tool calls: {report.round1_tool_count}")
    print(f"Unique tools: {report.round1_unique_tools}")
    print(f"Round 2 tool calls (probe): {report.round2_tool_count}")
    print(f"PASS single-turn (>={args.min_tools} tools): {report.pass_single_turn}")
    print(f"PASS diversity (>={args.min_unique} unique): {report.pass_tool_diversity}")
    print(f"OVERALL: {report.pass_single_turn and report.pass_tool_diversity}")
    if report.notes:
        print("Notes:")
        for n in report.notes:
            print(f"  - {n}")
    return 0 if (report.pass_single_turn and report.pass_tool_diversity) else 1


if __name__ == "__main__":
    raise SystemExit(main())
