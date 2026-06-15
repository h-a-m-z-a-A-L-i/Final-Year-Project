import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.streaming import filter_tool_calls_for_mode


def test_filter_tool_calls_drops_outside_agentic():
    calls = [{"function": {"name": "insert_cell", "arguments": "{}"}}]
    assert filter_tool_calls_for_mode(calls, "ask") == []
    assert filter_tool_calls_for_mode(calls, "code") == []
    assert filter_tool_calls_for_mode([], "agentic") == []


def test_filter_tool_calls_keeps_in_agentic(monkeypatch):
    from testing.host import agentic_mode as am

    monkeypatch.setattr(am, "LLM_AGENTIC_ENABLED", True)
    calls = [{"function": {"name": "insert_cell", "arguments": "{}"}}]
    kept = filter_tool_calls_for_mode(calls, "agentic")
    assert len(kept) == 1
    assert kept[0]["function"]["name"] == "insert_cell"
