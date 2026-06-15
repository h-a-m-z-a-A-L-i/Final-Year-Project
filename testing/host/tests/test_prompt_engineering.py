import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import prompt_engineering as pe


def test_detect_mode_uses_ui_selection_only():
    assert pe.detect_mode("write code for cell 3", "ask") == "ask"
    assert pe.detect_mode("write code for cell 3", "code") == "code"
    assert pe.detect_mode("anything", None) == "ask"


def test_legacy_auto_alias_to_ask():
    assert pe.normalize_mode("auto") == "ask"
    assert pe.detect_mode("any text", "auto") == "ask"


def test_legacy_mode_aliases_to_ask():
    assert pe.normalize_mode("simple") == "ask"
    assert pe.normalize_mode("explain_error") == "ask"


def test_classify_ask_intent():
    assert pe.classify_ask_intent("where should I insert this code") == "placement"
    assert pe.classify_ask_intent("fix traceback in cell 2") == "error"
    assert pe.classify_ask_intent("upstream dependencies for cell 3") == "dependency"
    assert pe.classify_ask_intent("review my notebook for leakage") == "review"
    assert pe.classify_ask_intent("what does cell 1 do") == "explain"


def test_build_system_includes_jupyter_model():
    content = pe.build_system_content("ask", notebook_url="https://example.com/x/edit", context="cell 1")
    assert "Jupyter notebook model" in content
    assert "Insert" in content or "insert" in content


def test_build_system_follows_schema_order():
    content = pe.build_system_content("ask", notebook_url="https://example.com/x/edit", context="cell 1")
    role_pos = content.find("## Role")
    task_pos = content.find("## Task")
    specifics_pos = content.find("## Specifics")
    context_pos = content.find("## Context")
    examples_pos = content.find("## Examples")
    notes_pos = content.find("## Notes")
    assert role_pos < task_pos < specifics_pos < context_pos < examples_pos < notes_pos
    assert "INSUFFICIENT_CONTEXT" in content
    assert "https://example.com/x/edit" in content
    assert "cell 1" in content


def test_build_chat_messages_order():
    msgs = pe.build_chat_messages(
        mode="ask",
        user_prompt="hi",
        history=[{"role": "user", "content": "old"}],
        notebook_url="https://example.com/edit",
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "hi"


def test_react_browser_prompt_sections_when_enabled(monkeypatch):
    monkeypatch.setattr(pe, "LLM_AGENTIC_ENABLED", True)
    try:
        from testing.host import agentic_mode as am
        monkeypatch.setattr(am, "LLM_AGENTIC_ENABLED", True)
        am.set_dashboard_agentic_enabled(True)
    except Exception:
        pass
    content = pe.build_system_content(
        "agentic",
        notebook_url="https://www.kaggle.com/code/x/edit",
        context="cell 1",
    )
    assert "Agentic" in content or "agentic" in content.lower()
    assert "insert_cell" in content


def test_agentic_system_notes_require_single_turn_batch(monkeypatch):
    monkeypatch.setattr(pe, "LLM_AGENTIC_ENABLED", True)
    try:
        from testing.host import agentic_mode as am
        monkeypatch.setattr(am, "LLM_AGENTIC_ENABLED", True)
        am.set_dashboard_agentic_enabled(True)
    except Exception:
        pass
    content = pe.build_system_content(
        "agentic",
        notebook_url="https://www.kaggle.com/code/x/edit",
        context="cell 1",
    )
    assert "never one tool per round" in content.lower()
    assert "all tool_calls" in content.lower() or "multiple" in content.lower()
    assert "edit_cell_by_index" in content
    assert "insert_and_edit_cell example" not in content


def test_ask_mode_excludes_react_browser_even_when_flag_set(monkeypatch):
    monkeypatch.setattr(pe, "LLM_AGENTIC_ENABLED", True)
    content = pe.build_system_content("ask", notebook_url="https://example.com/x/edit")
    assert "Observe → Think → Act" not in content


def test_code_mode_excludes_browser_tools_when_agentic_flag_set(monkeypatch):
    monkeypatch.setattr(pe, "LLM_AGENTIC_ENABLED", True)
    content = pe.build_system_content("code", notebook_url="https://example.com/x/edit")
    assert "Agentic mode is **active**" not in content


def test_parse_prompt_sections():
    raw = "## Role\nYou are X.\n\n## Task\nStep 1.\n\n## Notes\nBe careful."
    secs = pe.parse_prompt_sections(raw)
    assert "You are X" in secs["role"]
    assert "Step 1" in secs["task"]
    assert "Be careful" in secs["notes"]


def test_agentic_glm_native_prompt_excludes_text_batch(monkeypatch):
    monkeypatch.setattr(pe, "LLM_AGENTIC_ENABLED", True)
    monkeypatch.setenv("CEREBRAS_MODEL", "zai-glm-4.7")
    content = pe.build_system_content(
        "agentic",
        notebook_url="https://www.kaggle.com/code/x/edit",
        include_tools=True,
        text_tool_calls=False,
    )
    assert "native parallel tool_calls" in content.lower() or "native `tool_calls`" in content
    assert "Emit one <agent_tool_batch>" not in content
    assert '<agent_tool_batch>\n[' not in content


def test_agentic_gpt_oss_text_batch_prompt_when_enabled(monkeypatch):
    monkeypatch.setattr(pe, "LLM_AGENTIC_ENABLED", True)
    monkeypatch.setenv("CEREBRAS_MODEL", "gpt-oss-120b")
    content = pe.build_system_content(
        "agentic",
        notebook_url="https://www.kaggle.com/code/x/edit",
        include_tools=True,
        text_tool_calls=True,
    )
    assert "<agent_tool_batch>" in content
