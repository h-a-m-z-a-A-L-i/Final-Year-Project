import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_text_tools import (
    inject_tool_defaults,
    parse_text_tool_batch,
    strip_tool_batch_from_text,
    text_tool_calling_enabled,
)


def test_parse_text_tool_batch():
    text = """
Here are the tools:
<agent_tool_batch>
[
  {"tool": "insert_cell", "args": {"index": 2, "direction": "below", "url": "https://x/edit"}},
  {"tool": "run_cell", "args": {"cell_index": 3, "url": "https://x/edit"}}
]
</agent_tool_batch>
"""
    calls = parse_text_tool_batch(text)
    assert len(calls) == 2
    assert calls[0]["function"]["name"] == "insert_cell"
    assert calls[1]["function"]["name"] == "run_cell"
    assert "index" in calls[0]["function"]["arguments"]


def test_strip_tool_batch():
    text = "hello<agent_tool_batch>[]</agent_tool_batch>world"
    assert strip_tool_batch_from_text(text) == "helloworld"


def test_text_tool_calling_default_cerebras_agentic():
    assert text_tool_calling_enabled("cerebras", agentic=True) is True
    assert text_tool_calling_enabled("cerebras", agentic=False) is False


def test_inject_defaults():
    calls = [{"id": "1", "type": "function", "function": {"name": "run_cell", "arguments": "{}"}}]
    out = inject_tool_defaults(calls, url="https://x/edit", tab_id=7)
    args = json.loads(out[0]["function"]["arguments"])
    assert args["url"] == "https://x/edit"
    assert args["tab_id"] == 7
