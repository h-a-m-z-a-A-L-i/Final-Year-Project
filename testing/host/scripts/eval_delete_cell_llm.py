#!/usr/bin/env python3
"""
Evaluate whether the LLM calls delete_by_index correctly vs our delete pipeline.

Runs agentic prompts that require deleting a cell on the Pakistan housing notebook.
Uses real read tools + real delete tool (host + extension must be running for live delete).
Reports whether failure is LLM-side (wrong/missing tool) or host-side (tool called but ok:false).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import testing.host.agentic_mode as agentic_mode  # noqa: E402
from testing.host.agentic_mode import set_dashboard_agentic_enabled  # noqa: E402
from testing.host.config import (  # noqa: E402
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_REACT_MAX_ROUNDS,
    TEMPERATURE,
    TOP_P,
    _LLM_CLIENT,
)
from testing.host.llm_provider import react_min_interval_sec  # noqa: E402
from testing.host.notebook_context import pack_context  # noqa: E402
from testing.host.prompt_engineering import agentic_runtime_enabled, build_chat_messages  # noqa: E402
from testing.host.streaming import (  # noqa: E402
    _completion_extra_kwargs,
    _final_text_from_response,
    _parallel_tool_calls_flag,
)
from testing.host.tool_registry import BROWSER_TOOL_NAMES, registry  # noqa: E402

URL = "https://www.kaggle.com/code/codekey/pakistan-housing/edit"

# Cell 51 is a short label cell ("49\ncatboost") — safe delete target in eval prompts.
DELETE_TARGET_CELL = 51


@dataclass
class ToolCallRecord:
    round: int
    name: str
    args: dict
    result_ok: bool
    result: dict = field(default_factory=dict)


@dataclass
class DeleteEvalResult:
    scenario_id: str
    prompt: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_text: str = ""
    llm_called_delete: bool = False
    delete_args_correct: bool = False
    delete_host_ok: bool = False
    verdict: str = ""
    error: str | None = None


def _execute_tool(fname: str, args: dict) -> dict:
    args = dict(args or {})
    args.setdefault("url", URL)
    reg = registry()
    try:
        return reg.call(fname, args)
    except Exception as e:
        return {"ok": False, "error": str(e), "tool": fname}


def run_llm_delete_prompt(
    prompt: str,
    max_rounds: int,
    min_interval: float,
    target_cell: int,
) -> DeleteEvalResult:
    result = DeleteEvalResult(scenario_id="delete_by_index", prompt=prompt)
    if _LLM_CLIENT is None:
        result.error = "No LLM client configured"
        result.verdict = "config_error"
        return result

    agentic_mode.LLM_AGENTIC_ENABLED = True
    set_dashboard_agentic_enabled(True)
    if not agentic_runtime_enabled("agentic"):
        result.error = "Agentic gate failed"
        result.verdict = "config_error"
        return result

    pack = pack_context(mode="agentic", url=URL, prompt=prompt)
    messages = build_chat_messages(
        mode="agentic",
        user_prompt=prompt,
        history=[],
        context=pack.text,
        notebook_url=URL,
        include_tools=True,
    )
    from testing.host.tool_registry import build_cerebras_tools

    tools = build_cerebras_tools(include_browser=True)
    parallel = _parallel_tool_calls_flag()
    extra = _completion_extra_kwargs()
    tool_messages = list(messages)
    final_text = ""

    for round_i in range(max_rounds):
        if round_i > 0 and min_interval > 0:
            time.sleep(min_interval)
        try:
            resp = _LLM_CLIENT.chat.completions.create(
                messages=tool_messages,
                model=LLM_MODEL,
                tools=tools,
                parallel_tool_calls=parallel,
                temperature=min(TEMPERATURE, 0.3),
                top_p=TOP_P,
                **extra,
            )
        except Exception as e:
            result.error = str(e)
            result.verdict = "llm_api_error"
            result.tool_calls = result.tool_calls
            return result

        dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
        choice = (dumped.get("choices") or [{}])[0]
        assistant_msg = choice.get("message") or {}
        tool_calls = assistant_msg.get("tool_calls") or []
        if not tool_calls:
            final_text = _final_text_from_response(resp).strip()
            break

        tool_messages.append({
            "role": "assistant",
            "content": assistant_msg.get("content") or "",
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc.get("function") or {}
            fname = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except json.JSONDecodeError:
                parsed = {}
            tool_result = _execute_tool(fname, parsed)
            result.tool_calls.append(
                ToolCallRecord(
                    round=round_i + 1,
                    name=fname,
                    args=parsed,
                    result_ok=bool(tool_result.get("ok")),
                    result=tool_result,
                )
            )
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or f"call_{fname}_{round_i}",
                "content": json.dumps(tool_result, ensure_ascii=False),
            })

    result.final_text = final_text
    delete_calls = [c for c in result.tool_calls if c.name == "delete_by_index"]
    result.llm_called_delete = bool(delete_calls)
    if delete_calls:
        idx = delete_calls[0].args.get("cell_index")
        try:
            result.delete_args_correct = abs(int(idx) - target_cell) <= 1
        except (TypeError, ValueError):
            result.delete_args_correct = False
        result.delete_host_ok = delete_calls[0].result_ok

    if not result.llm_called_delete:
        result.verdict = "llm_side — model did not call delete_by_index"
    elif not result.delete_args_correct:
        result.verdict = "llm_side — wrong cell_index in delete_by_index"
    elif not result.delete_host_ok:
        err = (delete_calls[0].result or {}).get("error", "unknown")
        result.verdict = f"host_side — delete_by_index failed: {err}"
    else:
        result.verdict = "ok — LLM called delete_by_index and host reported success"

    return result


def _safe(s: str, limit: int = 0) -> str:
    text = (s or "").encode("ascii", errors="replace").decode("ascii")
    return text[:limit] if limit else text


def print_result(r: DeleteEvalResult, target_cell: int) -> None:
    print("=" * 72)
    print("DELETE CELL — LLM vs HOST DIAGNOSTIC")
    print("=" * 72)
    print(f"Provider : {LLM_PROVIDER} / {LLM_MODEL}")
    print(f"Notebook : {URL}")
    print(f"Target   : cell {target_cell}")
    print(f"Prompt   : {r.prompt}")
    print()
    print(f"LLM called delete_by_index : {r.llm_called_delete}")
    print(f"Correct cell_index         : {r.delete_args_correct}")
    print(f"Host delete ok             : {r.delete_host_ok}")
    print(f"VERDICT                    : {r.verdict}")
    if r.error:
        print(f"Error                      : {r.error}")
    print()
    print("Tool sequence:")
    for tc in r.tool_calls:
        brief = {k: tc.args.get(k) for k in ("cell_index", "index", "url", "query") if k in tc.args}
        status = "ok" if tc.result_ok else "FAIL"
        print(f"  r{tc.round} [{status}] {tc.name} {brief}")
        if tc.name == "delete_by_index" and not tc.result_ok:
            print(f"         host error: {(tc.result or {}).get('error')}")
    if r.final_text:
        print(f"\nFinal: {_safe(r.final_text, 300)}")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM delete cell diagnostic")
    parser.add_argument("--cell-index", type=int, default=DELETE_TARGET_CELL)
    parser.add_argument("--max-rounds", type=int, default=LLM_REACT_MAX_ROUNDS)
    parser.add_argument("--min-interval", type=float, default=None)
    parser.add_argument("--dry-run-llm", action="store_true", help="Skip LLM; only test host delete tool")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    target_cell = args.cell_index

    if args.dry_run_llm:
        print("Dry run: calling delete_by_index directly (no LLM)...")
        out = _execute_tool("delete_by_index", {"url": URL, "cell_index": target_cell})
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1

    min_interval = args.min_interval
    if min_interval is None:
        min_interval = react_min_interval_sec(LLM_PROVIDER)

    prompt = (
        f"Delete cell {target_cell} from this notebook using the delete tool. "
        f"Use delete_by_index with the exact 1-based cell index. Confirm when done."
    )
    result = run_llm_delete_prompt(prompt, args.max_rounds, min_interval, target_cell)
    print_result(result, target_cell)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(asdict(result), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if "llm_side" in result.verdict:
        return 2
    if "host_side" in result.verdict:
        return 3
    return 0 if result.delete_host_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
