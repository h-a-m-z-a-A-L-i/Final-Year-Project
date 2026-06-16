"""Optional Gemini explicit context cache (google-genai SDK).

Reduces billed input tokens on repeated ReAct turns when the static system prefix
(notebook evidence + tool schemas) is unchanged. Requires: pip install google-genai

Set GEMINI_CONTEXT_CACHE=1 in .env. Falls back silently if SDK unavailable.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

_CACHE_LOCK = threading.Lock()
_CACHE_STORE: dict[str, dict[str, Any]] = {}


def _cache_key(model: str, system_text: str) -> str:
    digest = hashlib.sha256(system_text.encode("utf-8")).hexdigest()[:24]
    return f"{model}:{digest}"


def get_cached_content_name(model: str, system_text: str, *, ttl_seconds: int = 600) -> str | None:
    """Return a cachedContents resource name for reuse, or None if caching unavailable."""
    if not str(system_text or "").strip():
        return None

    key = _cache_key(model, system_text)
    now = time.time()
    with _CACHE_LOCK:
        row = _CACHE_STORE.get(key)
        if row and row.get("expires_at", 0) > now:
            return row.get("name")

    try:
        from google import genai
        from google.genai import types
    except Exception:
        return None

    api_key = __import__("os").environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        client = genai.Client(api_key=api_key)
        cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                display_name=f"notebook-copilot-{key[:12]}",
                system_instruction=system_text,
                ttl=f"{int(ttl_seconds)}s",
            ),
        )
        name = getattr(cache, "name", None) or (cache.get("name") if isinstance(cache, dict) else None)
        if not name:
            return None
        with _CACHE_LOCK:
            _CACHE_STORE[key] = {"name": name, "expires_at": now + ttl_seconds}
        return name
    except Exception:
        return None
