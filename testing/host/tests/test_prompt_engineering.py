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


def test_parse_prompt_sections():
    raw = "## Role\nYou are X.\n\n## Task\nStep 1.\n\n## Notes\nBe careful."
    secs = pe.parse_prompt_sections(raw)
    assert "You are X" in secs["role"]
    assert "Step 1" in secs["task"]
    assert "Be careful" in secs["notes"]
