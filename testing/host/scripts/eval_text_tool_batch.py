#!/usr/bin/env python3
"""Eval text-format tool batch (chat-only, no API tools param)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.agentic_mode import set_dashboard_agentic_enabled
from testing.host.agentic_text_tools import inject_tool_defaults, parse_text_tool_batch, text_tool_calling_enabled
from testing.host.config import LLM_MODEL, LLM_PROVIDER, TEMPERATURE, TOP_P, _LLM_CLIENT
from testing.host.prompt_engineering import agentic_runtime_enabled, build_chat_messages
from testing.host.streaming import _completion_extra_kwargs, _final_text_from_response

URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"
PROMPT = (
    "Cell indices known. insert below 2, edit 3 print('batch_eval_1'), "
    "insert below 3, edit 4 print('batch_eval_2'), run 3, run 4. "
    "Use <agent_tool_batch> with all tools."
)


def main() -> int:
    assert text_tool_calling_enabled(LLM_PROVIDER, agentic=True)
    set_dashboard_agentic_enabled(True)
    messages = build_chat_messages(
        mode="agentic",
        user_prompt=PROMPT,
        history=[],
        context="cells 1-25",
        notebook_url=URL,
        include_tools=True,
        text_tool_calls=True,
        turn_tail="Emit <agent_tool_batch> with every tool in one JSON array.",
    )
    extra = _completion_extra_kwargs()
    resp = _LLM_CLIENT.chat.completions.create(
        messages=messages,
        model=LLM_MODEL,
        temperature=0,
        top_p=TOP_P,
        **extra,
    )
    content = _final_text_from_response(resp)
    calls = parse_text_tool_batch(content)
    calls = inject_tool_defaults(calls, url=URL, tab_id=None)
    names = [(c.get("function") or {}).get("name") for c in calls]
    print("text_tools=True provider=", LLM_PROVIDER)
    print("parsed_count", len(calls))
    print("tools", names)
    print("content_preview", content[:800])
    ok = len(calls) >= 6 and len(set(names)) >= 3
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
