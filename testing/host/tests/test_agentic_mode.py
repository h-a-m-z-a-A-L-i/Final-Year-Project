import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import agentic_mode as am


def test_agentic_requires_mode_and_server(monkeypatch, tmp_path):
    settings = tmp_path / "agentic_settings.json"
    monkeypatch.setattr(am, "_SETTINGS_PATH", settings)
    monkeypatch.setattr(am, "LLM_AGENTIC_ENABLED", True)
    assert am.agentic_session_active("agentic")
    assert not am.agentic_session_active("code")
    assert not am.agentic_session_active("ask")
    monkeypatch.setattr(am, "LLM_AGENTIC_ENABLED", False)
    assert not am.agentic_session_active("agentic")


def test_resolve_downgrades_agentic_when_server_off(monkeypatch, tmp_path):
    settings = tmp_path / "agentic_settings.json"
    monkeypatch.setattr(am, "_SETTINGS_PATH", settings)
    monkeypatch.setattr(am, "LLM_AGENTIC_ENABLED", False)
    mode, warn = am.resolve_effective_chat_mode("agentic")
    assert mode == "code"
    assert warn


def test_browser_tool_blocked_outside_agentic(monkeypatch, tmp_path):
    settings = tmp_path / "agentic_settings.json"
    monkeypatch.setattr(am, "_SETTINGS_PATH", settings)
    monkeypatch.setattr(am, "LLM_AGENTIC_ENABLED", True)
    ok, err = am.browser_tool_allowed("code", "edit_cell_by_index")
    assert not ok
    assert err
    ok2, err2 = am.browser_tool_allowed("agentic", "edit_cell_by_index")
    assert ok2
    assert err2 is None
