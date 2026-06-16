"""Multi-tool LLM response: parse, enrich safety net, implied-action counting."""

import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_action_guard import (
    build_incomplete_batch_nudge,
    count_implied_tool_actions,
)
from testing.host.agentic_batch_executor import (
    INTER_TOOL_DELAY_SEC,
    ParsedToolCall,
    _parse_tool_calls,
    enrich_batch_from_prompt,
    reorder_parsed_runs_last,
    reorder_tool_calls_runs_last,
    should_use_batch_executor,
)


def test_count_implied_actions_delete_and_insert():
    prompt = "delete cell 2 and create new cell under cell 1"
    assert count_implied_tool_actions(prompt) == 2


def test_count_implied_actions_single_delete():
    assert count_implied_tool_actions("delete cell 2") == 1


def test_parse_native_two_tool_calls():
    raw = [
        {
            "id": "call_del",
            "type": "function",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 2}),
            },
        },
        {
            "id": "call_ins",
            "type": "function",
            "function": {
                "name": "insert_cell",
                "arguments": json.dumps({"index": 1, "direction": "below"}),
            },
        },
    ]
    parsed = _parse_tool_calls(raw, url="https://example.com/edit", tab_id=None)
    assert len(parsed) == 2
    names = [c.name for c in parsed]
    assert names == ["delete_by_index", "insert_cell"]


def test_should_use_batch_executor_for_two_writes():
    tool_calls = [
        {
            "id": "1",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 2}),
            },
        },
        {
            "id": "2",
            "function": {
                "name": "insert_cell",
                "arguments": json.dumps({"index": 1, "direction": "below"}),
            },
        },
    ]
    assert should_use_batch_executor(tool_calls, agentic_active=True) is True


def test_enrich_batch_adds_missing_insert_for_delete_and_create_prompt():
    calls = [
        ParsedToolCall("1", "delete_by_index", {"cell_index": 2, "url": "https://example.com/edit"}),
    ]
    out = enrich_batch_from_prompt(
        calls,
        user_prompt="delete cell 2 and create new cell under cell 1",
        url="https://example.com/edit",
        tab_id=None,
    )
    names = [c.name for c in out]
    assert "delete_by_index" in names
    assert "insert_cell" in names
    insert = next(c for c in out if c.name == "insert_cell")
    assert insert.args.get("index") == 1
    assert insert.args.get("direction") == "below"


def test_enrich_batch_adds_missing_delete_for_combined_prompt():
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 1, "direction": "below", "url": "https://example.com/edit"}),
    ]
    out = enrich_batch_from_prompt(
        calls,
        user_prompt="delete cell 2 and create new cell under cell 1",
        url="https://example.com/edit",
        tab_id=None,
    )
    names = [c.name for c in out]
    assert "delete_by_index" in names
    assert "insert_cell" in names
    delete = next(c for c in out if c.name == "delete_by_index")
    assert delete.args.get("cell_index") == 2


def test_incomplete_batch_nudge_mentions_parallel_tool_calls():
    nudge = build_incomplete_batch_nudge(
        "delete cell 2 and create new cell under cell 1",
        parsed_count=1,
        implied_count=2,
        parsed_tools=["delete_by_index"],
        use_text_tools=False,
    )
    assert "parallel_tool_calls" in nudge.lower()
    assert "2" in nudge


def test_reorder_puts_run_cell_after_writes():
    raw = [
        {
            "id": "call_run",
            "function": {
                "name": "run_cell",
                "arguments": json.dumps({"cell_index": 3}),
            },
        },
        {
            "id": "call_del",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 2}),
            },
        },
        {
            "id": "call_ins",
            "function": {
                "name": "insert_cell",
                "arguments": json.dumps({"index": 1, "direction": "below"}),
            },
        },
    ]
    reordered, changed = reorder_tool_calls_runs_last(raw)
    assert changed is True
    names = [(tc.get("function") or {}).get("name") for tc in reordered]
    assert names == ["delete_by_index", "insert_cell", "run_cell"]


def test_reorder_parsed_stable_within_groups():
    calls = [
        ParsedToolCall("1", "run_cell", {"cell_index": 3}),
        ParsedToolCall("2", "delete_by_index", {"cell_index": 2}),
        ParsedToolCall("3", "run_cell", {"cell_index": 5}),
        ParsedToolCall("4", "insert_cell", {"index": 1}),
    ]
    reordered, changed = reorder_parsed_runs_last(calls)
    assert changed is True
    assert [c.name for c in reordered] == [
        "delete_by_index",
        "insert_cell",
        "run_cell",
        "run_cell",
    ]
    assert [c.args["cell_index"] for c in reordered if c.name == "run_cell"] == [3, 5]


def test_reorder_delete_before_insert_when_insert_first():
    raw = [
        {
            "id": "call_ins",
            "function": {
                "name": "insert_cell",
                "arguments": json.dumps({"index": 1, "direction": "below"}),
            },
        },
        {
            "id": "call_del",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 2}),
            },
        },
    ]
    from testing.host.agentic_batch_executor import reorder_delete_before_insert

    reordered, changed = reorder_delete_before_insert(raw)
    assert changed is True
    names = [(tc.get("function") or {}).get("name") for tc in reordered]
    assert names == ["delete_by_index", "insert_cell"]


def test_reorder_deletes_descending_highest_first():
    raw = [
        {
            "id": "d1",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 3}),
            },
        },
        {
            "id": "d2",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 13}),
            },
        },
        {
            "id": "d3",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 8}),
            },
        },
        {
            "id": "d4",
            "function": {
                "name": "delete_by_index",
                "arguments": json.dumps({"cell_index": 4}),
            },
        },
    ]
    from testing.host.agentic_batch_executor import reorder_deletes_descending

    reordered, changed = reorder_deletes_descending(raw)
    assert changed is True
    indices = [
        json.loads((tc.get("function") or {}).get("arguments") or "{}").get("cell_index")
        for tc in reordered
    ]
    assert indices == [13, 8, 4, 3]


def test_reorder_inserts_descending_highest_first():
    raw = [
        {
            "id": "i1",
            "function": {
                "name": "insert_cell",
                "arguments": json.dumps({"index": 10, "direction": "below"}),
            },
        },
        {
            "id": "i2",
            "function": {
                "name": "insert_cell",
                "arguments": json.dumps({"index": 34, "direction": "below"}),
            },
        },
        {
            "id": "i3",
            "function": {
                "name": "insert_cell",
                "arguments": json.dumps({"index": 22, "direction": "below"}),
            },
        },
    ]
    from testing.host.agentic_batch_executor import reorder_inserts_descending

    reordered, changed = reorder_inserts_descending(raw)
    assert changed is True
    anchors = [
        json.loads((tc.get("function") or {}).get("arguments") or "{}").get("index")
        for tc in reordered
    ]
    assert anchors == [34, 22, 10]


def test_reorder_inserts_descending_user_order_175():
    """User prompt anchors 1, 7, 5 → host executes 7, 5, 1."""
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 1, "direction": "below"}),
        ParsedToolCall("2", "insert_cell", {"index": 7, "direction": "below"}),
        ParsedToolCall("3", "insert_cell", {"index": 5, "direction": "below"}),
    ]
    reordered, changed = reorder_parsed_runs_last(calls)
    assert changed is True
    anchors = [c.args["index"] for c in reordered if c.name == "insert_cell"]
    assert anchors == [7, 5, 1]


def test_enrich_batch_multi_insert_fixes_duplicate_anchors():
    prompt = "insert new cells at under cell 1, 7, 5"
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 7, "direction": "below"}),
        ParsedToolCall("2", "insert_cell", {"index": 7, "direction": "below"}),
        ParsedToolCall("3", "insert_cell", {"index": 7, "direction": "below"}),
    ]
    out = enrich_batch_from_prompt(
        calls,
        user_prompt=prompt,
        url="https://example.com/edit",
        tab_id=None,
    )
    inserts = [c for c in out if c.name == "insert_cell"]
    assert len(inserts) == 3
    assert [c.args["index"] for c in inserts] == [1, 7, 5]
    reordered, _ = reorder_parsed_runs_last(out)
    anchors = [c.args["index"] for c in reordered if c.name == "insert_cell"]
    assert anchors == [7, 5, 1]


def test_enrich_batch_multi_insert_from_prompt_only():
    prompt = "insert new cells at under cell 1, 7, 5"
    out = enrich_batch_from_prompt(
        [],
        user_prompt=prompt,
        url="https://example.com/edit",
        tab_id=42,
    )
    inserts = [c for c in out if c.name == "insert_cell"]
    assert len(inserts) == 3
    assert [c.args["index"] for c in inserts] == [1, 7, 5]
    assert all(c.args.get("tab_id") == 42 for c in inserts)


def test_tool_queue_delay_matches_config():
    from testing.host.config import TOOL_QUEUE_DELAY_SEC

    assert TOOL_QUEUE_DELAY_SEC == 1.0
    assert INTER_TOOL_DELAY_SEC == TOOL_QUEUE_DELAY_SEC
