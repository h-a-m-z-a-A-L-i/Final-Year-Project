"""Record actual LLM token usage from API responses (incl. Cerebras cached_tokens)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import DATA_ROOT, TOKEN_USAGE_LOG_PATH
except Exception:
    from config import DATA_ROOT, TOKEN_USAGE_LOG_PATH

_USAGE_LOCK = threading.Lock()


def _zero_usage() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
    }


def extract_usage_from_response(response: Any) -> dict[str, int]:
    """Parse OpenAI-compatible usage block (Cerebras includes prompt_tokens_details.cached_tokens)."""
    usage = _zero_usage()
    if response is None:
        return usage

    raw = None
    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump()
            raw = dumped.get("usage") if isinstance(dumped, dict) else None
        except Exception:
            raw = None
    if raw is None:
        raw = getattr(response, "usage", None)
    if raw is None and isinstance(response, dict):
        raw = response.get("usage")

    if raw is None:
        return usage

    if hasattr(raw, "model_dump"):
        try:
            raw = raw.model_dump()
        except Exception:
            pass

    if not isinstance(raw, dict):
        try:
            raw = {
                "prompt_tokens": getattr(raw, "prompt_tokens", 0),
                "completion_tokens": getattr(raw, "completion_tokens", 0),
                "total_tokens": getattr(raw, "total_tokens", 0),
                "prompt_tokens_details": getattr(raw, "prompt_tokens_details", None),
            }
        except Exception:
            return usage

    usage["prompt_tokens"] = int(raw.get("prompt_tokens") or 0)
    usage["completion_tokens"] = int(raw.get("completion_tokens") or 0)
    usage["total_tokens"] = int(raw.get("total_tokens") or 0)
    if usage["total_tokens"] <= 0:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

    details = raw.get("prompt_tokens_details") or {}
    if hasattr(details, "model_dump"):
        try:
            details = details.model_dump()
        except Exception:
            details = {}
    if isinstance(details, dict):
        usage["cached_tokens"] = int(details.get("cached_tokens") or 0)

    return usage


def merge_usage(into: dict[str, int], addition: dict[str, int] | None) -> dict[str, int]:
    if not addition:
        return into
    for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens"):
        into[key] = int(into.get(key, 0) or 0) + int(addition.get(key, 0) or 0)
    into["requests"] = int(into.get("requests", 0) or 0) + 1
    return into


def format_usage_line(usage: dict[str, int], *, estimated: int | None = None) -> str:
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = int(usage.get("cached_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    parts = [
        f"prompt={prompt}",
        f"completion={completion}",
        f"cached={cached}",
        f"total={total}",
    ]
    if estimated is not None and total <= 0:
        parts.append(f"est={estimated}")
    if cached > 0 and prompt > 0:
        pct = int(round(100.0 * cached / max(prompt, 1)))
        parts.append(f"cache_hit={pct}%")
    return "Token usage: " + ", ".join(parts)


def billable_tokens(usage: dict[str, int] | None, fallback: int = 0) -> int:
    if usage and int(usage.get("total_tokens") or 0) > 0:
        return int(usage["total_tokens"])
    return int(fallback or 0)


def record_token_event(
    *,
    attempt_id: str = "",
    session_id: str = "",
    history_key: str = "",
    mode: str = "",
    label: str = "chat",
    usage: dict[str, int] | None = None,
    estimated_tokens: int = 0,
) -> None:
    """Append one API call to token_usage.jsonl."""
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attempt_id": attempt_id,
        "session_id": session_id,
        "history_key": history_key,
        "mode": mode,
        "label": label,
        "usage": usage or _zero_usage(),
        "estimated_tokens": int(estimated_tokens or 0),
        "billable_tokens": billable_tokens(usage, estimated_tokens),
    }
    path = Path(TOKEN_USAGE_LOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False)
    with _USAGE_LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def read_usage_totals(*, session_id: str | None = None, hours: int = 24) -> dict[str, int]:
    """Sum billable tokens from the log (optional session filter)."""
    path = Path(TOKEN_USAGE_LOG_PATH)
    if not path.is_file():
        return {**_zero_usage(), "requests": 0}

    cutoff = datetime.now(timezone.utc).timestamp() - max(1, hours) * 3600
    totals = _zero_usage()
    totals["requests"] = 0
    sid = str(session_id or "").strip()

    with _USAGE_LOCK:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            try:
                ts = datetime.fromisoformat(str(row.get("timestamp", ""))).timestamp()
            except Exception:
                continue
            if ts < cutoff:
                continue
            if sid and str(row.get("session_id") or "") != sid:
                continue
            usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
            bill = int(row.get("billable_tokens") or usage.get("total_tokens") or 0)
            totals["total_tokens"] += bill
            totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            totals["cached_tokens"] += int(usage.get("cached_tokens") or 0)
            totals["requests"] += 1

    return totals
