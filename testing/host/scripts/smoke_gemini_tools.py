#!/usr/bin/env python3
"""Verify Gemini OpenAI-compatible API: plain, stream, and tool calling."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.config import (  # noqa: E402
    GEMINI_MODEL,
    LLM_MODEL,
    LLM_PROVIDER,
    TEMPERATURE,
    TOP_P,
    _LLM_CLIENT,
)
from testing.host.tool_registry import build_cerebras_tools  # noqa: E402
from testing.host.llm_provider import parallel_tool_calls_enabled  # noqa: E402

URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"


def _dump_tool_calls(resp) -> list[dict]:
    dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
    choice = (dumped.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return msg.get("tool_calls") or []


def test_plain() -> bool:
    print("\n=== 1. Plain completion ===")
    resp = _LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": "Say exactly: plain_ok"}],
        max_tokens=32,
        temperature=0,
    )
    text = (resp.choices[0].message.content or "").strip()
    print("content:", repr(text))
    ok = "plain_ok" in text.lower()
    print("PASS" if ok else "FAIL")
    return ok


def test_stream() -> bool:
    print("\n=== 2. Streaming completion ===")
    stream = _LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": "Say exactly: stream_ok"}],
        stream=True,
        max_tokens=32,
        temperature=0,
    )
    parts = []
    for event in stream:
        delta = getattr(getattr(event.choices[0], "delta", None), "content", None) if event.choices else None
        if delta:
            parts.append(delta)
    text = "".join(parts).strip()
    print("content:", repr(text))
    ok = "stream_ok" in text.lower()
    print("PASS" if ok else "FAIL")
    return ok


def test_tools_no_stream() -> bool:
    print("\n=== 3. Tool calling (non-stream) ===")
    tools = build_cerebras_tools(include_browser=True)
    # Use a small subset for the test
    browser_tools = [t for t in tools if t["function"]["name"] in ("insert_cell", "edit_cell_by_index")]
    print(f"tools exposed: {[t['function']['name'] for t in browser_tools]}")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a notebook agent. When asked to insert a cell, call insert_cell only. "
                f"Always pass url={URL!r}."
            ),
        },
        {
            "role": "user",
            "content": "Insert an empty code cell below cell index 2.",
        },
    ]
    parallel = parallel_tool_calls_enabled(LLM_PROVIDER)
    print("parallel_tool_calls:", parallel)

    try:
        resp = _LLM_CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=browser_tools,
            parallel_tool_calls=parallel,
            temperature=TEMPERATURE,
            top_p=TOP_P,
        )
    except Exception as e:
        print("API ERROR:", e)
        return False

    tool_calls = _dump_tool_calls(resp)
    content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    print("assistant content:", repr(content[:200]))
    print("tool_calls:", json.dumps(tool_calls, indent=2)[:1500])
    ok = len(tool_calls) >= 1 and tool_calls[0].get("function", {}).get("name") == "insert_cell"
    print("PASS" if ok else "FAIL")
    return ok


def test_tools_roundtrip() -> bool:
    print("\n=== 4. Tool roundtrip (call + tool result + follow-up) ===")
    tools = build_cerebras_tools(include_browser=True)
    browser_tools = [t for t in tools if t["function"]["name"] in ("insert_cell", "edit_cell_by_index")]

    messages = [
        {
            "role": "system",
            "content": f"You call tools to edit notebooks. url is always {URL}.",
        },
        {"role": "user", "content": "Insert below cell 2, then you will get tool result."},
    ]
    parallel = parallel_tool_calls_enabled(LLM_PROVIDER)

    resp1 = _LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        tools=browser_tools,
        parallel_tool_calls=parallel,
        temperature=0.2,
    )
    tool_calls = _dump_tool_calls(resp1)
    if not tool_calls:
        print("No tool_calls in round 1")
        return False

    tc = tool_calls[0]
    fn = tc.get("function") or {}
    fname = fn.get("name")
    raw_args = fn.get("arguments") or "{}"
    print("round1 tool:", fname, raw_args[:200])

    messages.append({
        "role": "assistant",
        "content": resp1.choices[0].message.content or "",
        "tool_calls": tool_calls,
    })
    messages.append({
        "role": "tool",
        "tool_call_id": tc.get("id"),
        "content": json.dumps({
            "ok": True,
            "tool": fname,
            "new_cell_index": 3,
            "new_dom_index": 2,
        }),
    })

    try:
        resp2 = _LLM_CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=browser_tools,
            parallel_tool_calls=parallel,
            temperature=0.2,
        )
    except Exception as e:
        print("round2 API ERROR:", e)
        return False

    text2 = (resp2.choices[0].message.content or "").strip()
    tool_calls2 = _dump_tool_calls(resp2)
    print("round2 content:", repr(text2[:300]))
    print("round2 tool_calls:", len(tool_calls2))
    ok = bool(text2) or len(tool_calls2) > 0
    print("PASS" if ok else "FAIL")
    return ok


def main() -> int:
    print("provider:", LLM_PROVIDER, "model:", LLM_MODEL)
    if _LLM_CLIENT is None:
        print("No LLM client", file=sys.stderr)
        return 1

    results = [
        test_plain(),
        test_stream(),
        test_tools_no_stream(),
        test_tools_roundtrip(),
    ]
    print("\n=== Summary ===")
    names = ["plain", "stream", "tools", "roundtrip"]
    for name, ok in zip(names, results):
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
