"""Google AI Studio (Gemini) OpenAI-compatible client stub.

Set in .env:
  LLM_PROVIDER=google
  GEMINI_API_KEY=...
  GEMINI_MODEL=gemini-3.1-flash-lite

Install: pip install google-genai  (or openai if using compatibility endpoint)
"""

from __future__ import annotations

import os
from typing import Any


def create_google_client() -> Any | None:
    api_key = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not api_key:
        return None

    # Prefer OpenAI-compatible shim when available (works with streaming tool_calls).
    try:
        from openai import OpenAI

        base = os.environ.get(
            "GEMINI_OPENAI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ).strip()
        return OpenAI(api_key=api_key, base_url=base)
    except Exception:
        pass

    try:
        from google import genai

        return genai.Client(api_key=api_key)
    except Exception:
        return None
