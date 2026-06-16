"""Provider-specific LLM settings (Cerebras vs Google Gemini)."""

from __future__ import annotations

import os

# Cerebras free tier: hard cap — do not raise without a billing/plan upgrade.
CEREBRAS_RPM_HARD_LIMIT = 5

# Default Cerebras model (Z.ai GLM 4.7 — native parallel tool calling).
CEREBRAS_DEFAULT_MODEL = "zai-glm-4.7"

# Models that support parallel native tool_calls in one API response (see cerebras_parallel_tool_investigation).
_NATIVE_PARALLEL_MODEL_PREFIXES = ("zai-glm-",)

# Free-tier TPM defaults per Cerebras model family.
_CEREBRAS_TPM_DEFAULTS = {
    "zai-glm-4.7": 30_000,   # GLM free trial: 30k input tokens/min
    "gpt-oss-120b": 60_000,
}

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


def normalize_cerebras_model(model: str | None = None) -> str:
    raw = str(model or os.environ.get("CEREBRAS_MODEL", CEREBRAS_DEFAULT_MODEL)).strip().lower()
    return raw or CEREBRAS_DEFAULT_MODEL


def cerebras_supports_native_parallel_tools(model: str | None = None) -> bool:
    """True when the model can emit multiple tool_calls in one completion (e.g. zai-glm-4.7)."""
    mid = normalize_cerebras_model(model)
    return any(mid.startswith(prefix) for prefix in _NATIVE_PARALLEL_MODEL_PREFIXES)


def cerebras_uses_text_tool_batch(model: str | None = None) -> bool:
    """Text <agent_tool_batch> fallback for models without reliable native multi-tool (e.g. gpt-oss)."""
    return not cerebras_supports_native_parallel_tools(model)


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
    env = os.environ.get("LLM_PARALLEL_TOOL_CALLS", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    if normalize_provider(provider) == "google":
        return True
    if agentic and normalize_provider(provider) == "cerebras":
        return cerebras_supports_native_parallel_tools()
    return False


def cerebras_hard_rpm_limit() -> int:
    """Hardcoded requests-per-minute ceiling for Cerebras (free tier)."""
    return CEREBRAS_RPM_HARD_LIMIT


def cerebras_rate_limits(model: str | None = None) -> dict[str, int]:
    """Local tracker defaults for Cerebras; RPM is not overridable via env."""
    mid = normalize_cerebras_model(model)
    tpm_default = _CEREBRAS_TPM_DEFAULTS.get(mid)
    if tpm_default is None:
        tpm_default = 30_000 if cerebras_supports_native_parallel_tools(mid) else 60_000
    return {
        "rpm": CEREBRAS_RPM_HARD_LIMIT,
        "tpm": int(os.environ.get("CEREBRAS_TPM_LIMIT", str(tpm_default))),
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
    if raw == "none":
        return "none"
    return raw if raw in ("low", "medium", "high") else "low"


def _cerebras_clear_thinking(*, mode: str | None) -> bool:
    """GLM clear_thinking: exclude prior thinking from context when True (API default)."""
    env = os.environ.get("CEREBRAS_CLEAR_THINKING", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    # Agentic tool loops benefit from retaining prior reasoning across turns.
    if str(mode or "").strip().lower() == "agentic":
        return False
    return True


def cerebras_completion_extras(
    *,
    session_id: str | None = None,
    mode: str | None = None,
    model: str | None = None,
) -> dict:
    """Cerebras SDK options from api/ docs (reasoning, GLM clear_thinking, prompt cache)."""
    mid = normalize_cerebras_model(model)
    effort = cerebras_reasoning_effort()
    extra: dict = {}

    if cerebras_supports_native_parallel_tools(mid):
        # Z.ai GLM 4.7 — reasoning on by default; use reasoning_effort="none" to disable.
        extra["reasoning_effort"] = effort
        extra["clear_thinking"] = _cerebras_clear_thinking(mode=mode)
    else:
        # GPT-OSS and legacy models
        extra["reasoning_format"] = "hidden"
        extra["reasoning_effort"] = effort if effort != "none" else "low"

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
