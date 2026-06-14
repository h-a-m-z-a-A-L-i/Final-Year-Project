#!/usr/bin/env python3
"""Verify Cerebras API: plain, tools (strict), and rate-limit settings."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.config import (  # noqa: E402
    LLM_MODEL,
    LLM_PROVIDER,
    RPM_LIMIT,
    TEMPERATURE,
    TOP_P,
    _LLM_CLIENT,
)
from testing.host.llm_provider import (  # noqa: E402
    CEREBRAS_RPM_HARD_LIMIT,
    cerebras_completion_extras,
    react_min_interval_sec,
)
from testing.host.streaming import _completion_extra_kwargs  # noqa: E402
from testing.host.tool_registry import build_cerebras_tools  # noqa: E402

URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"


def test_config() -> bool:
    print("\n=== 0. Config ===")
    print("provider:", LLM_PROVIDER, "model:", LLM_MODEL)
    print("RPM_LIMIT:", RPM_LIMIT, "hard:", CEREBRAS_RPM_HARD_LIMIT)
    print("react interval:", react_min_interval_sec(LLM_PROVIDER), "s")
    ok = LLM_PROVIDER == "cerebras" and RPM_LIMIT == 5 and _LLM_CLIENT is not None
    print("PASS" if ok else "FAIL")
    return ok


def test_plain() -> bool:
    print("\n=== 1. Plain completion ===")
    extra = _completion_extra_kwargs(session_id="smoke-cerebras")
    resp = _LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": "Say exactly: cerebras_ok"}],
        max_tokens=32,
        temperature=0,
        **extra,
    )
    text = (resp.choices[0].message.content or "").strip()
    print("content:", repr(text))
    ok = "cerebras_ok" in text.lower()
    print("PASS" if ok else "FAIL")
    return ok


def test_tools_strict() -> bool:
    print("\n=== 2. Tool calling (strict) ===")
    tools = build_cerebras_tools(include_browser=True, strict=True)
    browser = [t for t in tools if t["function"]["name"] in ("insert_cell", "edit_cell_by_index")]
    strict_flags = [t["function"].get("strict") for t in browser]
    print("tools:", [t["function"]["name"] for t in browser])
    print("strict:", strict_flags)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a notebook agent. Call insert_cell when asked to insert a cell. "
                f"Always pass url={URL!r}."
            ),
        },
        {"role": "user", "content": "Insert an empty code cell below cell index 2."},
    ]
    extra = cerebras_completion_extras(session_id="smoke-cerebras-tools")
    resp = _LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        tools=browser,
        parallel_tool_calls=False,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        **extra,
    )
    dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
    choice = (dumped.get("choices") or [{}])[0]
    tool_calls = (choice.get("message") or {}).get("tool_calls") or []
    print("tool_calls:", json.dumps(tool_calls, indent=2)[:1200])
    ok = (
        len(tool_calls) >= 1
        and tool_calls[0].get("function", {}).get("name") == "insert_cell"
        and all(s is True for s in strict_flags)
    )
    print("PASS" if ok else "FAIL")
    return ok


def main() -> int:
    if not test_config():
        return 1
    spacing = react_min_interval_sec(LLM_PROVIDER)
    results = [test_plain()]
    print(f"\n(waiting {spacing:.0f}s for 5 RPM spacing...)")
    time.sleep(spacing)
    results.append(test_tools_strict())
    print("\n=== Summary ===")
    for name, ok in zip(["plain", "tools_strict"], results):
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
