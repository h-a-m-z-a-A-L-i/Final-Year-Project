#!/usr/bin/env python3
"""Trace how many tools the LLM emits per round until idle (max 8 rounds)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.agentic_mode import set_dashboard_agentic_enabled
from testing.host.config import LLM_MODEL, LLM_PROVIDER, TEMPERATURE, TOP_P, _LLM_CLIENT
from testing.host.prompt_engineering import agentic_runtime_enabled, build_chat_messages
from testing.host.streaming import _completion_extra_kwargs, _parallel_tool_calls_flag
from testing.host.tool_registry import build_cerebras_tools

URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"
PROMPT = (
    "Cell indices are known. In ONE tool response emit ALL of these: "
    "notebook_list_cells, insert_cell below 2, edit_cell_by_index on cell 3 with print('a'), "
    "insert_cell below 3, edit_cell_by_index on cell 4 with print('b'), run_cell 3, run_cell 4. "
    "Do not split across rounds."
)


def main() -> int:
    set_dashboard_agentic_enabled(True)
    parallel = _parallel_tool_calls_flag(agentic=True)
    messages = build_chat_messages(
        mode="agentic",
        user_prompt=PROMPT,
        history=[],
        context="Cells 1-25 exist. Cell 2 and 3 are code.",
        notebook_url=URL,
        include_tools=True,
    )
    tools = build_cerebras_tools(include_browser=True)
    extra = _completion_extra_kwargs()
    tool_messages = list(messages)
    rounds: list[dict] = []

    for i in range(8):
        resp = _LLM_CLIENT.chat.completions.create(
            messages=tool_messages,
            model=LLM_MODEL,
            tools=tools,
            parallel_tool_calls=parallel,
            tool_choice="required" if i == 0 else "auto",
            temperature=TEMPERATURE,
            top_p=TOP_P,
            **extra,
        )
        msg = (resp.model_dump().get("choices") or [{}])[0].get("message") or {}
        tcs = msg.get("tool_calls") or []
        names = [(tc.get("function") or {}).get("name") for tc in tcs]
        rounds.append({"round": i + 1, "count": len(tcs), "tools": names, "text": (msg.get("content") or "")[:80]})
        if not tcs:
            break
        tool_messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
        for tc in tcs:
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or f"r{i}",
                "content": json.dumps({"ok": True, "mock": True}),
            })

    total = sum(r["count"] for r in rounds)
    print(json.dumps({
        "provider": LLM_PROVIDER,
        "model": LLM_MODEL,
        "parallel_tool_calls": parallel,
        "rounds": rounds,
        "total_tool_calls": total,
        "all_in_round1": rounds[0]["count"] if rounds else 0,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
