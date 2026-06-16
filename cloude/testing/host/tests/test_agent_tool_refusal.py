"""Tests for agent_tool_refusal classification (no API)."""

from testing.host.agent_tool_refusal import classify_tool_refusal_failure
from testing.host.agentic_text_tools_types import TextToolParseResult


def test_classify_prose_only():
    ft = classify_tool_refusal_failure(
        raw_model_response="**Placement**\n- Insert below cell 23.\n**Code**\n```python\nprint(1)\n```",
        parse_result=TextToolParseResult(),
    )
    assert ft == "PROSE_ONLY"


def test_classify_tool_refusal():
    ft = classify_tool_refusal_failure(
        raw_model_response="I cannot use tools to edit the notebook directly.",
        parse_result=TextToolParseResult(),
    )
    assert ft == "TOOL_REFUSAL"


def test_classify_malformed_batch():
    raw = '<agent_tool_batch>[{not json}]</agent_tool_batch>'
    pr = TextToolParseResult(batch_count=1, parse_errors=["batch_1: Expecting value"])
    ft = classify_tool_refusal_failure(raw_model_response=raw, parse_result=pr)
    assert ft == "MALFORMED_BATCH"


def test_classify_unknown_tool_only():
    pr = TextToolParseResult(unknown_tools=["notebook_edit"], batch_count=1)
    pr.parse_errors.append("item_0: unknown")
    ft = classify_tool_refusal_failure(
        raw_model_response='<agent_tool_batch>[{"tool":"notebook_edit"}]</agent_tool_batch>',
        parse_result=pr,
    )
    assert ft == "UNKNOWN_TOOL_ONLY"


def test_classify_empty_batch():
    pr = TextToolParseResult(batch_count=1)
    ft = classify_tool_refusal_failure(
        raw_model_response="<agent_tool_batch>[]</agent_tool_batch>",
        parse_result=pr,
    )
    assert ft == "EMPTY_BATCH"
