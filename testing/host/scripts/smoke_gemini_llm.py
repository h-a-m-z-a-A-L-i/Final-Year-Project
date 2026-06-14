#!/usr/bin/env python3
"""Quick connectivity check for Gemini via OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.host.config import GEMINI_API_KEY, GEMINI_MODEL, LLM_MODEL, LLM_PROVIDER, _LLM_CLIENT  # noqa: E402
from testing.host.llm_provider import gemini_free_tier_limits, parallel_tool_calls_enabled  # noqa: E402


def main() -> int:
    print("LLM_PROVIDER:", LLM_PROVIDER)
    print("LLM_MODEL:", LLM_MODEL)
    print("GEMINI_MODEL:", GEMINI_MODEL)
    print("Limits:", json.dumps(gemini_free_tier_limits(GEMINI_MODEL), indent=2))
    print("parallel_tool_calls:", parallel_tool_calls_enabled(LLM_PROVIDER))

    if _LLM_CLIENT is None:
        print("ERROR: LLM client not initialized. Set GEMINI_API_KEY in .env", file=sys.stderr)
        return 1
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY missing", file=sys.stderr)
        return 1

    resp = _LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": "Reply with exactly: gemini_ok"}],
        max_tokens=16,
        temperature=0,
    )
    text = (resp.choices[0].message.content or "").strip()
    print("Response:", text)
    return 0 if "gemini_ok" in text.lower() else 2


if __name__ == "__main__":
    raise SystemExit(main())
