"""AIML API client (OpenAI-compatible). Configure via .env: AIML_API_KEY, AIML_MODEL."""

import os

from openai import OpenAI

AIML_API_BASE_URL = os.environ.get("AIML_API_BASE_URL", "https://api.aimlapi.com/v1").strip()
AIML_API_KEY = os.environ.get("AIML_API_KEY", "").strip()
AIML_MODEL = os.environ.get("AIML_MODEL", "x-ai/grok-4-1-fast-reasoning").strip()


def create_aiml_client() -> OpenAI | None:
    if not AIML_API_KEY:
        return None
    return OpenAI(base_url=AIML_API_BASE_URL, api_key=AIML_API_KEY)


if __name__ == "__main__":
    try:
        from .config import _LLM_CLIENT, LLM_MODEL
    except ImportError:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from testing.host.config import _LLM_CLIENT, LLM_MODEL

    client = _LLM_CLIENT
    if client is None:
        raise SystemExit("Set AIML_API_KEY in .env before running this script.")

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "You are an AI assistant who knows everything."},
            {"role": "user", "content": "Tell me, why is the sky blue?"},
        ],
    )
    print(f"Assistant: {response.choices[0].message.content}")
