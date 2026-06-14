"""Provider-specific LLM settings (Cerebras vs Google Gemini)."""

from __future__ import annotations

import os

# Cerebras free tier: hard cap — do not raise without a billing/plan upgrade.
CEREBRAS_RPM_HARD_LIMIT = 5

# API model IDs (Google AI Studio). User-facing names may differ from API strings.
GEMINI_MODEL_ALIASES = {
    "gemini-3.5-flash": "gemini-2.5-flash",
    "gemini-3.1-flash-lite": "gemini-2.5-flash-lite",
    "gemini-3.1-flash": "gemini-2.5-flash",
}

# Free-tier defaults per model (RPM / RPD). TPM is shared at 250k across models.
_GEMINI_FREE_TIER = {
    "gemini-2.5-flash-lite": {"rpm": 15, "rpd": 1000},
    "gemini-2.5-flash": {"rpm": 10, "rpd": 250},
    "gemini-2.5-pro": {"rpm": 5, "rpd": 100},
    "default": {"rpm": 15, "rpd": 1000},
}


def normalize_provider(raw: str | None) -> str:
    p = str(raw or "cerebras").strip().lower()
    return p if p in {"cerebras", "google"} else "cerebras"


def resolve_gemini_model_id(model: str) -> str:
    m = str(model or "").strip().lower()
    return GEMINI_MODEL_ALIASES.get(m, m or "gemini-2.5-flash-lite")


def gemini_free_tier_limits(model: str) -> dict[str, int]:
    mid = resolve_gemini_model_id(model)
    row = _GEMINI_FREE_TIER.get(mid) or _GEMINI_FREE_TIER["default"]
    tpm = int(os.environ.get("GEMINI_TPM_LIMIT", "250000"))
    return {
        "tpm": tpm,
        "rpm": int(os.environ.get("GEMINI_RPM_LIMIT", str(row["rpm"]))),
        "rpd": int(os.environ.get("GEMINI_RPD_LIMIT", str(row["rpd"]))),
        "tph": int(os.environ.get("GEMINI_TPH_LIMIT", str(tpm * 60))),
        "rph": int(os.environ.get("GEMINI_RPH_LIMIT", "9000")),
    }


def parallel_tool_calls_enabled(provider: str, *, agentic: bool = False) -> bool:
    if os.environ.get("LLM_PARALLEL_TOOL_CALLS", "").strip().lower() in ("0", "false", "no"):
        return False
    if os.environ.get("LLM_PARALLEL_TOOL_CALLS", "").strip().lower() in ("1", "true", "yes"):
        return True
    if agentic and normalize_provider(provider) == "cerebras":
        return True
    return normalize_provider(provider) == "google"


def cerebras_hard_rpm_limit() -> int:
    """Hardcoded requests-per-minute ceiling for Cerebras (free tier)."""
    return CEREBRAS_RPM_HARD_LIMIT


def cerebras_rate_limits() -> dict[str, int]:
    """Local tracker defaults for Cerebras; RPM is not overridable via env."""
    return {
        "rpm": CEREBRAS_RPM_HARD_LIMIT,
        "tpm": int(os.environ.get("CEREBRAS_TPM_LIMIT", "60000")),
        "rph": int(os.environ.get("CEREBRAS_RPH_LIMIT", "900")),
        "rpd": int(os.environ.get("CEREBRAS_RPD_LIMIT", "14400")),
    }


def react_min_interval_sec(provider: str) -> float:
    """Minimum spacing between LLM calls (ReAct loop + streaming)."""
    p = normalize_provider(provider)
    if p == "google":
        try:
            return max(0.0, float(os.environ.get("GEMINI_REACT_MIN_INTERVAL_SEC", "4")))
        except Exception:
            return 4.0
    if p == "cerebras":
        # 5 req/min => at least 12s between calls (hardcoded; not env-overridable).
        return 60.0 / float(CEREBRAS_RPM_HARD_LIMIT)
    return 0.0


def cerebras_reasoning_effort() -> str:
    raw = os.environ.get("CEREBRAS_REASONING_EFFORT", "low").strip().lower()
    return raw if raw in ("low", "medium", "high") else "low"


def cerebras_completion_extras(
    *,
    session_id: str | None = None,
    mode: str | None = None,
) -> dict:
    """Cerebras SDK options from api/ docs (reasoning, prompt cache via extra_body)."""
    extra: dict = {
        "reasoning_format": "hidden",
        "reasoning_effort": cerebras_reasoning_effort(),
    }
    sid = str(session_id or "").strip()
    if sid and os.environ.get("CEREBRAS_PROMPT_CACHE", "1").strip().lower() in ("1", "true", "yes"):
        mode_tag = str(mode or "").strip().lower()
        cache_key = f"nb-copilot:{sid[:960]}"
        if mode_tag:
            cache_key = f"{cache_key}:{mode_tag[:32]}"
        extra["extra_body"] = {"prompt_cache_key": cache_key[:1024]}
    return extra


def provider_display_name(provider: str) -> str:
    return "Gemini" if normalize_provider(provider) == "google" else "Cerebras"
