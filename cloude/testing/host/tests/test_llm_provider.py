import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.llm_provider import (
    CEREBRAS_DEFAULT_MODEL,
    cerebras_completion_extras,
    cerebras_rate_limits,
    cerebras_supports_native_parallel_tools,
    cerebras_uses_text_tool_batch,
    parallel_tool_calls_enabled,
    resolve_gemini_model_id,
    gemini_free_tier_limits,
    cerebras_hard_rpm_limit,
    react_min_interval_sec,
)


def test_cerebras_hard_rpm():
    assert cerebras_hard_rpm_limit() == 5


def test_cerebras_react_interval():
    assert react_min_interval_sec("cerebras") == 12.0


def test_resolve_gemini_aliases():
    assert resolve_gemini_model_id("gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"
    assert resolve_gemini_model_id("gemini-2.5-flash-lite") == "gemini-2.5-flash-lite"


def test_gemini_flash_lite_limits():
    lim = gemini_free_tier_limits("gemini-2.5-flash-lite")
    assert lim["rpm"] == 15
    assert lim["rpd"] == 1000
    assert lim["tpm"] == 250000


def test_glm_native_parallel_tools(monkeypatch):
    monkeypatch.setenv("CEREBRAS_MODEL", "zai-glm-4.7")
    assert cerebras_supports_native_parallel_tools() is True
    assert cerebras_uses_text_tool_batch() is False
    assert cerebras_rate_limits()["tpm"] == 30_000


def test_gpt_oss_uses_text_batch(monkeypatch):
    monkeypatch.setenv("CEREBRAS_MODEL", "gpt-oss-120b")
    assert cerebras_supports_native_parallel_tools() is False
    assert cerebras_uses_text_tool_batch() is True
    assert cerebras_rate_limits()["tpm"] == 60_000


def test_parallel_tools_default_by_provider(monkeypatch):
    monkeypatch.delenv("LLM_PARALLEL_TOOL_CALLS", raising=False)
    monkeypatch.setenv("CEREBRAS_MODEL", "zai-glm-4.7")
    assert parallel_tool_calls_enabled("google") is True
    assert parallel_tool_calls_enabled("cerebras") is False
    assert parallel_tool_calls_enabled("cerebras", agentic=True) is True
    monkeypatch.setenv("CEREBRAS_MODEL", "gpt-oss-120b")
    assert parallel_tool_calls_enabled("cerebras", agentic=True) is False


def test_glm_completion_extras_agentic(monkeypatch):
    monkeypatch.setenv("CEREBRAS_MODEL", CEREBRAS_DEFAULT_MODEL)
    extra = cerebras_completion_extras(session_id="s1", mode="agentic", model="zai-glm-4.7")
    assert extra["reasoning_effort"] == "low"
    assert extra["clear_thinking"] is False
    assert "reasoning_format" not in extra


def test_gpt_oss_completion_extras(monkeypatch):
    extra = cerebras_completion_extras(mode="ask", model="gpt-oss-120b")
    assert extra["reasoning_format"] == "hidden"
    assert "clear_thinking" not in extra
