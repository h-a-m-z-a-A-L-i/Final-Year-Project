import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.llm_provider import (
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
    assert resolve_gemini_model_id("gemini-3.1-flash-lite") == "gemini-2.5-flash-lite"
    assert resolve_gemini_model_id("gemini-2.5-flash-lite") == "gemini-2.5-flash-lite"


def test_gemini_flash_lite_limits():
    lim = gemini_free_tier_limits("gemini-2.5-flash-lite")
    assert lim["rpm"] == 15
    assert lim["rpd"] == 1000
    assert lim["tpm"] == 250000


def test_parallel_tools_default_by_provider(monkeypatch):
    monkeypatch.delenv("LLM_PARALLEL_TOOL_CALLS", raising=False)
    assert parallel_tool_calls_enabled("google") is True
    assert parallel_tool_calls_enabled("cerebras") is False
