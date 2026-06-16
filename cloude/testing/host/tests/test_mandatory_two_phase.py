"""Mandatory two-phase agentic: round 0 query-only, round 1 implement-only."""

import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_action_guard import (
    build_auto_injected_query_tool_call,
    build_phase0_query_nudge,
    build_phase1_implementation_nudge,
    extract_target_cell_index_from_prompt,
    is_phase0_query_only_batch,
    should_force_implementation_batch,
    split_missing_inserts,
)
from testing.host.agentic_batch_executor import (
    ParsedToolCall,
    enrich_batch_from_prompt,
    enrich_split_cell_batch,
    filter_tools_for_phase,
    force_implementation_batch_from_prompt,
)
from testing.host.agentic_tool_chain import (
    extract_delete_cell_indices,
    extract_insert_cell_indices,
    prompt_requests_delete,
    recalculate_bulk_delete_indices,
)


def test_extract_target_cell_index_from_prompt():
    assert extract_target_cell_index_from_prompt("Fix cell 39 error") == 39
    assert extract_target_cell_index_from_prompt("Split cell 38 into 3") == 38


def test_phase0_query_nudge_mentions_no_writes():
    nudge = build_phase0_query_nudge("Split cell 38 into 3 smaller cells")
    assert "Phase 1" in nudge or "query only" in nudge.lower()
    assert "notebook_get_cell" in nudge
    assert "do NOT" in nudge or "not" in nudge.lower()


def test_phase1_implementation_nudge_for_split():
    nudge = build_phase1_implementation_nudge("Split cell 38 into 3 smaller cells")
    assert "Phase 2" in nudge
    assert "insert" in nudge.lower()
    assert "notebook_list_cells" in nudge


def test_filter_tools_phase0_strips_writes():
    url = "https://example.com/edit"
    raw = [
        {
            "id": "1",
            "function": {
                "name": "edit_cell_by_index",
                "arguments": json.dumps({"url": url, "cell_index": 38, "content": "x"}),
            },
        },
        {
            "id": "2",
            "function": {
                "name": "run_cell",
                "arguments": json.dumps({"url": url, "cell_index": 38}),
            },
        },
    ]
    filtered, meta = filter_tools_for_phase(
        0,
        raw,
        prompt="Fix cell 38 and run it",
        url=url,
        mandatory_two_phase=True,
    )
    names = [c["function"]["name"] for c in filtered]
    assert meta["writes_stripped"] is True
    assert meta["auto_injected"] is True
    assert names == ["notebook_get_cell"]
    assert is_phase0_query_only_batch(names)


def test_filter_tools_phase0_keeps_query_tools():
    url = "https://example.com/edit"
    raw = [
        {
            "id": "1",
            "function": {
                "name": "notebook_get_cell",
                "arguments": json.dumps({"url": url, "cell_index": 39}),
            },
        },
    ]
    filtered, meta = filter_tools_for_phase(
        0,
        raw,
        prompt="Fix cell 39",
        url=url,
        mandatory_two_phase=True,
    )
    assert meta["writes_stripped"] is False
    assert filtered == raw


def test_filter_tools_phase1_strips_queries():
    url = "https://example.com/edit"
    raw = [
        {
            "id": "1",
            "function": {
                "name": "notebook_list_cells",
                "arguments": json.dumps({"url": url}),
            },
        },
        {
            "id": "2",
            "function": {
                "name": "edit_cell_by_index",
                "arguments": json.dumps({"url": url, "cell_index": 39, "content": "x=1"}),
            },
        },
    ]
    filtered, meta = filter_tools_for_phase(
        1,
        raw,
        prompt="Fix cell 39",
        url=url,
        mandatory_two_phase=True,
    )
    names = [c["function"]["name"] for c in filtered]
    assert meta["queries_stripped"] is True
    assert names == ["edit_cell_by_index"]


def test_auto_inject_get_cell_for_named_cell():
    tc = build_auto_injected_query_tool_call(
        "Split cell 38 into 3",
        url="https://example.com/edit",
    )
    assert tc["function"]["name"] == "notebook_get_cell"
    args = json.loads(tc["function"]["arguments"])
    assert args["cell_index"] == 38


def test_mandatory_two_phase_forces_implementation_on_round1():
    assert should_force_implementation_batch(
        prompt="Fix cell 39",
        parsed_tools=["notebook_list_cells"],
        query_rounds_used=0,
        max_query_rounds=1,
        cumulative_has_writes=False,
        round_idx=1,
        max_tool_rounds=2,
        mandatory_two_phase=True,
    )


def test_split_missing_inserts_detects_edit_only():
    prompt = "Split cell 38 into 3 smaller cells"
    calls = [
        ParsedToolCall("1", "edit_cell_by_index", {"cell_index": 38, "content": "import pandas"}),
    ]
    assert split_missing_inserts(prompt, calls) is True


def test_enrich_split_cell_batch_adds_inserts():
    reg = type("R", (), {})()
    reg.call = lambda name, args: {
        "ok": True,
        "input": "import pandas as pd\n\ndf = pd.read_csv('x.csv')\n\nmodel = LinearRegression()",
    }
    calls = [
        ParsedToolCall("1", "edit_cell_by_index", {"cell_index": 38, "content": "import pandas as pd"}),
    ]
    out = enrich_split_cell_batch(
        calls,
        user_prompt="Split cell 38 into 3 smaller cells",
        url="https://example.com/edit",
        tab_id=1,
        registry=reg,
    )
    names = [c.name for c in out]
    assert names.count("insert_cell") == 2
    assert names.count("edit_cell_by_index") == 3


def test_prompt_requests_delete_plural_cells():
    assert prompt_requests_delete("remove cells 2, 4, 7, 3, 6, 5")
    assert prompt_requests_delete("delete cell 2")


def test_extract_delete_cell_indices_bulk():
    assert extract_delete_cell_indices("remove cells 2,4,,7,3,6,5") == [2, 4, 7, 3, 6, 5]
    assert extract_delete_cell_indices("delete cell 9") == [9]


def test_extract_insert_cell_indices_bulk():
    assert extract_insert_cell_indices("insert new cells at under cell 1, 7, 5") == [1, 7, 5]
    assert extract_insert_cell_indices("insert under cells 1, 7, 5") == [1, 7, 5]
    assert extract_insert_cell_indices("create new cell under cell 2") == [2]


def test_recalculate_bulk_delete_indices_user_order():
    original = [2, 4, 7, 3, 6, 5]
    assert recalculate_bulk_delete_indices(original) == [2, 3, 5, 2, 3, 2]


def test_force_implementation_bulk_delete_after_round0_query():
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    prompt = "remove cells 2,4,,7,3,6,5"
    out = force_implementation_batch_from_prompt(
        [],
        user_prompt=prompt,
        url=url,
        tab_id=2015941739,
        registry=None,
    )
    deletes = [c for c in out if c.name == "delete_by_index"]
    assert len(deletes) == 6
    assert [c.args["cell_index"] for c in deletes] == [2, 4, 7, 3, 6, 5]
    assert all(c.args.get("url") == url for c in deletes)
    assert all(c.args.get("tab_id") == 2015941739 for c in deletes)


def test_enrich_batch_bulk_delete_uses_original_indices():
    url = "https://example.com/edit"
    out = enrich_batch_from_prompt(
        [],
        user_prompt="remove cells 2, 4, 7, 3, 6, 5",
        url=url,
        tab_id=99,
    )
    deletes = [c for c in out if c.name == "delete_by_index"]
    assert [c.args["cell_index"] for c in deletes] == [2, 4, 7, 3, 6, 5]


def test_execute_agentic_batch_phase0_skips_write_enrichment():
    """Round 0 must not auto-inject deletes from enrich_batch_from_prompt."""
    import json
    from unittest.mock import MagicMock, patch

    from testing.host.agentic_batch_executor import execute_agentic_batch

    url = "https://example.com/edit"
    registry = MagicMock()
    registry.call.return_value = {"ok": True, "cells": []}
    tool_calls = [
        {
            "id": "1",
            "function": {
                "name": "notebook_list_cells",
                "arguments": json.dumps({"url": url}),
            },
        },
    ]
    with patch("testing.host.agentic_batch_executor.AGENTIC_FIRE_AND_FORGET", True):
        execute_agentic_batch(
            tool_calls,
            user_prompt="remove cells 2,4,,7,3,6,5",
            url=url,
            tab_id=1,
            registry=registry,
            browser_tool_allowed=lambda _m, _t: (True, None),
            mode="agentic",
            inter_delay=0.0,
            trace_round=0,
        )
    assert registry.call.call_count == 1
    assert registry.call.call_args[0][0] == "notebook_list_cells"


def test_execute_agentic_batch_force_delete_batch_fire_and_forget():
    """Round 1 force batch: all deletes in one batch with fire_and_forget args."""
    import json
    from unittest.mock import MagicMock, patch

    from testing.host.agentic_batch_executor import execute_agentic_batch

    url = "https://example.com/edit"
    captured: list[tuple[str, dict]] = []
    registry = MagicMock()

    def _record_call(name, args):
        captured.append((str(name), dict(args)))
        return {
            "ok": True,
            "dispatched": True,
            "phase": "dispatched",
            "cell_index": args.get("cell_index"),
        }

    registry.call.side_effect = _record_call
    tool_calls = [
        {
            "id": "1",
            "function": {
                "name": "notebook_list_cells",
                "arguments": json.dumps({"url": url}),
            },
        },
    ]
    with patch("testing.host.agentic_batch_executor.AGENTIC_FIRE_AND_FORGET", True):
        out = execute_agentic_batch(
            tool_calls,
            user_prompt="remove cells 2,4,,7,3,6,5",
            url=url,
            tab_id=1,
            registry=registry,
            browser_tool_allowed=lambda _m, _t: (True, None),
            mode="agentic",
            inter_delay=0.0,
            trace_round=1,
            force_implementation=True,
        )
    delete_captured = [args for name, args in captured if name == "delete_by_index"]
    assert len(delete_captured) == 6
    assert [a["cell_index"] for a in delete_captured] == [7, 6, 5, 4, 3, 2]
    assert all(a.get("fire_and_forget") is True for a in delete_captured)
    delete_rows = [row for row in (out.get("executed") or []) if row.get("tool") == "delete_by_index"]
    assert len(delete_rows) == 6
    assert out.get("fire_and_forget") is True
