#!/usr/bin/env python3
"""Simulate agentic chat tool loop (no browser) to verify Gemini + tools end-to-end."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.agentic_mode import set_dashboard_agentic_enabled  # noqa: E402
from testing.host.config import LLM_MODEL, TEMPERATURE, TOP_P, _LLM_CLIENT  # noqa: E402
from testing.host.prompt_engineering import (  # noqa: E402
    agentic_runtime_enabled,
    build_chat_messages,
)
from testing.host.streaming import (  # noqa: E402
    _completion_extra_kwargs,
    _final_text_from_response,
    _parallel_tool_calls_flag,
)
from testing.host.tool_registry import build_cerebras_tools, registry  # noqa: E402

URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"
PROMPT = "insert a cell below cell 2 with print('hi')"


def mock_tool_call(fname: str, args: dict) -> dict:
    if fname == "insert_cell":
        return {
            "ok": True,
            "tool": "insert_cell",
            "new_cell_index": 3,
            "new_dom_index": 2,
            "direction": args.get("direction", "below"),
        }
    if fname == "edit_cell_by_index":
        return {
            "ok": True,
            "tool": "edit_cell_by_index",
            "cell_index": args.get("cell_index"),
            "phase": "content_set",
        }
    return {"ok": False, "error": f"mock skip {fname}"}


def main() -> int:
    set_dashboard_agentic_enabled(True)
    mode = "agentic"
    assert agentic_runtime_enabled(mode), "agentic gate failed"

    messages = build_chat_messages(
        mode=mode,
        user_prompt=PROMPT,
        history=[],
        context="Cell 1: imports\nCell 2: empty",
        notebook_url=URL,
        include_tools=True,
    )
    tools = build_cerebras_tools(include_browser=True)
    parallel = _parallel_tool_calls_flag()
    extra = _completion_extra_kwargs()
    reg = registry()

    tool_messages = list(messages)
    final_text = ""
    max_rounds = 6

    print("messages:", len(messages), "tools:", len(tools), "parallel:", parallel)

    for round_i in range(max_rounds):
        print(f"\n--- round {round_i + 1} ---")
        resp = _LLM_CLIENT.chat.completions.create(
            messages=tool_messages,
            model=LLM_MODEL,
            tools=tools,
            parallel_tool_calls=parallel,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            **extra,
        )
        dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
        choice = (dumped.get("choices") or [{}])[0]
        assistant_msg = choice.get("message") or {}
        tool_calls = assistant_msg.get("tool_calls") or []
        prose = _final_text_from_response(resp).strip()
        print("prose:", repr(prose[:120]))
        print("tool_calls:", [tc.get("function", {}).get("name") for tc in tool_calls])

        if not tool_calls:
            final_text = prose or final_text
            break

        tool_messages.append({
            "role": "assistant",
            "content": assistant_msg.get("content") or "",
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc.get("function") or {}
            fname = fn.get("name")
            raw_args = fn.get("arguments") or "{}"
            parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            parsed.setdefault("url", URL)
            result = mock_tool_call(fname, parsed)
            print(f"  mock {fname} -> ok={result.get('ok')}")
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": json.dumps(result, ensure_ascii=False),
            })

    if not final_text:
        final_resp = _LLM_CLIENT.chat.completions.create(
            messages=tool_messages,
            model=LLM_MODEL,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            **extra,
        )
        final_text = _final_text_from_response(final_resp).strip()

    print("\n=== FINAL ===")
    print(final_text or "(empty)")
    return 0 if final_text else 1


if __name__ == "__main__":
    raise SystemExit(main())
