import json
import os
import sys
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_batch_executor import (
    analyze_cell_output,
    build_batch_error_context,
    build_batch_success_verification,
    build_fire_and_forget_user_summary,
    build_fire_and_forget_verification,
    enrich_batch_from_prompt,
    execute_run_queue_dispatch_only,
    finalize_tool_queue_verification,
    fetch_queue_cell_evidence,
    execute_run_queue_sequential,
    expand_multi_cell_from_prompt,
    normalize_batch_indices,
    normalize_sequential_insert_anchors,
    resolve_batch_cell_indices,
    ParsedToolCall,
    partition_batch,
    split_batch_at_run,
    user_requests_run,
    verify_workflow_batch,
    wait_for_cell_run,
    workflow_needs_llm_followup,
    _attach_run_queue_verification,
    _ordered_run_indices,
    _run_wait_failed,
    _sort_tool_calls,
)
from testing.host.agentic_tool_chain import (
    extract_cell_count_from_prompt,
    parse_multi_cell_contents,
)


def test_sort_tool_calls_order():
    calls = [
        ParsedToolCall("3", "run_cell", {"cell_index": 2}),
        ParsedToolCall("1", "insert_cell", {"index": 1}),
        ParsedToolCall("2", "edit_cell_by_index", {"cell_index": 2, "content": "x=1"}),
    ]
    sorted_calls = _sort_tool_calls(calls)
    assert [c.name for c in sorted_calls] == [
        "insert_cell",
        "edit_cell_by_index",
        "run_cell",
    ]


def test_normalize_batch_indices_below():
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 2, "direction": "below"}),
        ParsedToolCall("2", "edit_cell_by_index", {"cell_index": 2, "content": "print(1)"}),
        ParsedToolCall("3", "run_cell", {"cell_index": 2}),
    ]
    out = normalize_batch_indices(calls)
    assert out[1].args["cell_index"] == 3
    assert out[2].args["cell_index"] == 3


def test_enrich_batch_adds_edit_and_run():
    calls = [ParsedToolCall("1", "insert_cell", {"index": 2, "direction": "below"})]
    out = enrich_batch_from_prompt(
        calls,
        user_prompt="insert below cell 2 with print('hi') and run it",
        url="https://example.com/edit",
        tab_id=None,
    )
    names = [c.name for c in out]
    assert "edit_cell_by_index" in names
    assert "run_cell" in names


def test_verify_workflow_insert_edit_run():
    before = {"cells": [{"index": 1, "input": "a", "type": "code"}]}
    after = {
        "cells": [
            {"index": 1, "input": "a", "type": "code"},
            {
                "index": 2,
                "input": "print('hi')",
                "type": "code",
                "output": "hi\n",
                "execution_order": 1,
            },
        ]
    }
    executed = [
        {"tool": "insert_cell", "dispatched": True},
        {"tool": "edit_cell_by_index", "dispatched": True},
        {"tool": "run_cell", "dispatched": True},
    ]
    run_wait = {"ok": True, "cell_index": 2, "output": "hi\n", "execution_order": 1, "run_verified": True, "run_succeeded": True}
    out = verify_workflow_batch(
        before_data=before,
        after_data=after,
        executed=executed,
        expected_edits={2: "print('hi')"},
        run_cell_index=2,
        run_wait=run_wait,
    )
    assert out["verified"] is True
    assert "hi" in str(out.get("cell_output"))


def test_analyze_cell_output_detects_traceback():
    output = (
        "Traceback (most recent call last):\n"
        "  File \"<stdin>\", line 1, in <module>\n"
        "NameError: name 'hamza' is not defined\n"
    )
    analysis = analyze_cell_output(output)
    assert analysis["has_error"] is True
    assert analysis["run_succeeded"] is False
    assert analysis["error_type"] == "NameError"
    assert "hamza" in str(analysis["error_summary"])


def test_analyze_cell_output_empty_is_pending_not_success():
    analysis = analyze_cell_output("")
    assert analysis["has_output"] is False
    assert analysis["run_succeeded"] is False
    assert analysis.get("pending") is True


def test_analyze_cell_output_success():
    analysis = analyze_cell_output("hello\n")
    assert analysis["has_error"] is False
    assert analysis["run_succeeded"] is True


def test_verify_workflow_run_error_fails_verified():
    before = {"cells": [{"index": 1, "input": "x", "type": "code"}]}
    after = {
        "cells": [
            {
                "index": 1,
                "input": "print(hamza)",
                "type": "code",
                "output": "NameError: name 'hamza' is not defined\n",
                "execution_order": 2,
            }
        ]
    }
    run_wait = {
        "ok": True,
        "run_verified": True,
        "run_completed": True,
        "run_succeeded": False,
        "has_error": True,
        "error_type": "NameError",
        "error_summary": "NameError: name 'hamza' is not defined",
        "output": after["cells"][0]["output"],
        "cell_index": 1,
        "execution_order": 2,
    }
    out = verify_workflow_batch(
        before_data=before,
        after_data=after,
        executed=[{"tool": "edit_cell_by_index"}, {"tool": "run_cell"}],
        expected_edits={1: "print(hamza)"},
        run_cell_index=1,
        run_wait=run_wait,
    )
    assert out["verified"] is False
    assert out["phase"] == "execution_error"
    assert out["needs_fix"] is True
    assert out["execution_error"]["error_type"] == "NameError"
    assert workflow_needs_llm_followup(out) is True


def test_workflow_needs_followup_false_when_verified_only():
    assert workflow_needs_llm_followup({"verified": True}) is False


def test_workflow_needs_followup_true_after_successful_batch():
    assert workflow_needs_llm_followup({"verified": True, "batch_executed": True}) is False
    assert workflow_needs_llm_followup({
        "verified": True,
        "batch_executed": True,
        "deferred_tool_calls": [{"tool": "insert_cell"}],
    }) is True


def test_workflow_needs_followup_false_when_queue_complete():
    assert workflow_needs_llm_followup({
        "verified": True,
        "batch_executed": True,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "await_llm_summary": True,
    }) is False


def test_workflow_needs_followup_true_after_run():
    assert workflow_needs_llm_followup({
        "verified": True,
        "run_queue_complete": True,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "await_llm_summary": True,
    }) is False
    assert workflow_needs_llm_followup({"verified": True, "run_completed": True}) is False


def test_workflow_needs_followup_true_on_run_queue_error():
    assert workflow_needs_llm_followup({
        "verified": False,
        "needs_fix": True,
        "run_queue_stopped": True,
    }) is True


def test_workflow_needs_followup_true_on_run_queue_complete():
    assert workflow_needs_llm_followup({
        "verified": True,
        "run_queue_complete": True,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "await_llm_summary": True,
    }) is False


def test_run_wait_failed_detects_traceback():
    assert _run_wait_failed({"ok": True, "run_verified": True, "output": "Traceback (most recent call last):\nNameError: x", "run_succeeded": False, "has_error": True}) is True
    assert _run_wait_failed({"ok": True, "run_verified": True, "output": "hello\n", "run_succeeded": True}) is False


def test_ordered_run_indices_preserves_emission():
    calls = [
        ParsedToolCall("1", "run_cell", {"cell_index": 23}),
        ParsedToolCall("2", "run_cell", {"cell_index": 24}),
    ]
    assert _ordered_run_indices(calls, [25]) == [23, 24, 25]


@patch("testing.host.agentic_batch_executor.wait_for_cell_run")
@patch("testing.host.agentic_batch_executor._dispatch_run_cell")
@patch("testing.host.agentic_batch_executor.load_notebook_snapshot")
@patch("testing.host.agentic_batch_executor.time.sleep")
def test_execute_run_queue_stops_on_error(mock_sleep, mock_snap, mock_dispatch, mock_wait):
    mock_snap.return_value = ({}, "live")
    mock_dispatch.return_value = {"ok": True}
    mock_wait.side_effect = [
        {"ok": True, "output": "1\n", "run_verified": True, "run_succeeded": True},
        {"ok": True, "output": "NameError: foo\n", "run_verified": True, "run_succeeded": False, "has_error": True},
    ]
    executed: list = []
    completed, waits, pending = execute_run_queue_sequential(
        [10, 11, 12],
        executed=executed,
        registry=MagicMock(),
        url="https://x/edit",
        tab_id=None,
        mode="agentic",
        browser_tool_allowed=lambda _m, _t: (True, None),
        inter_delay=0.0,
    )
    assert completed == [10, 11]
    assert pending == [12]
    assert len(waits) == 2
    assert mock_dispatch.call_count == 2
    assert mock_wait.call_count == 2


@patch("testing.host.agentic_batch_executor.wait_for_cell_run")
@patch("testing.host.agentic_batch_executor._dispatch_run_cell")
@patch("testing.host.agentic_batch_executor.load_notebook_snapshot")
@patch("testing.host.agentic_batch_executor.time.sleep")
def test_execute_run_queue_completes_all(mock_sleep, mock_snap, mock_dispatch, mock_wait):
    mock_snap.return_value = ({}, "live")
    mock_dispatch.return_value = {"ok": True}
    mock_wait.return_value = {"ok": True, "output": "ok\n", "run_verified": True, "run_succeeded": True}
    executed: list = []
    completed, waits, pending = execute_run_queue_sequential(
        [1, 2, 3],
        executed=executed,
        registry=MagicMock(),
        url="https://x/edit",
        tab_id=None,
        mode="agentic",
        browser_tool_allowed=lambda _m, _t: (True, None),
        inter_delay=0.0,
    )
    assert completed == [1, 2, 3]
    assert pending == []
    assert mock_dispatch.call_count == 3


def test_attach_run_queue_verification_success():
    registry = MagicMock()
    registry.call = lambda name, args: {
        "ok": True,
        "cell_index": args.get("cell_index"),
        "input": "print(1)",
        "output": "1\n",
        "type": "code",
    }
    base = verify_workflow_batch(
        before_data={"cells": []},
        after_data={"cells": []},
        executed=[{"tool": "run_cell"}],
        expected_edits={},
        run_cell_indices=[1, 2],
        run_waits=[
            {"ok": True, "output": "1\n", "run_verified": True, "run_succeeded": True, "cell_index": 1},
            {"ok": True, "output": "2\n", "run_verified": True, "run_succeeded": True, "cell_index": 2},
        ],
    )
    out = finalize_tool_queue_verification(
        base,
        registry=registry,
        url="https://x/edit",
        expected_edits={},
        run_requested=[1, 2],
        run_completed=[1, 2],
        run_pending=[],
        user_prompt="run cells 1 and 2",
        run_waits=[
            {"ok": True, "output": "1\n", "run_verified": True, "run_succeeded": True, "cell_index": 1},
            {"ok": True, "output": "2\n", "run_verified": True, "run_succeeded": True, "cell_index": 2},
        ],
    )
    assert out["tool_queue_complete"] is True
    assert len(out["queue_cell_evidence"]["cells"]) == 2
    assert out["target_cells"][0]["input"] == "print(1)"
    assert out["target_cells"][0]["run_verified"] is True
    assert out["target_cells"][1]["success"] is True


def test_attach_run_queue_verification_stopped():
    registry = MagicMock()
    registry.call = lambda name, args: {
        "ok": True,
        "cell_index": args.get("cell_index"),
        "input": "bad()",
        "output": "NameError: x",
        "type": "code",
    }
    base = verify_workflow_batch(
        before_data={"cells": []},
        after_data={"cells": []},
        executed=[{"tool": "run_cell"}],
        expected_edits={},
        run_cell_indices=[5],
        run_waits=[{"ok": True, "output": "NameError: x", "run_succeeded": False, "has_error": True}],
    )
    out = finalize_tool_queue_verification(
        base,
        registry=registry,
        url="https://x/edit",
        expected_edits={},
        run_requested=[5, 6, 7],
        run_completed=[5],
        run_pending=[6, 7],
    )
    assert out["tool_queue_status"] == "error"
    assert out["tool_queue_stopped"] is True
    assert out["pending_run_cells"] == [6, 7]
    assert out["await_llm_summary"] is False
    assert out["close_react_loop"] is False
    assert out["error_recovery"]["failed_cell_index"] == 5
    assert out["error_recovery"]["may_propagate"] is True


def test_fetch_queue_cell_evidence_shape():
    registry = MagicMock()
    registry.call = lambda name, args: {
        "cell_index": args["cell_index"],
        "input": "x=1",
        "output": "",
        "type": "code",
    }
    out = fetch_queue_cell_evidence(registry, "https://x/edit", [3, 4])
    assert out["count"] == 2
    assert out["cells"][0]["input"] == "x=1"


def test_workflow_needs_followup_tool_queue_complete():
    assert workflow_needs_llm_followup({
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "await_llm_summary": True,
    }) is False


def test_split_batch_at_run_defers_post_run_tools():
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 2}),
        ParsedToolCall("2", "edit_cell_by_index", {"cell_index": 3, "content": "x=1"}),
        ParsedToolCall("3", "run_cell", {"cell_index": 3}),
        ParsedToolCall("4", "insert_cell", {"index": 4}),
    ]
    execute, deferred = partition_batch(calls)
    assert [c.name for c in execute] == ["insert_cell", "edit_cell_by_index", "run_cell"]
    assert [c.name for c in deferred] == ["insert_cell"]


def test_partition_batch_keeps_multiple_runs():
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 2}),
        ParsedToolCall("2", "edit_cell_by_index", {"cell_index": 3, "content": "print(1)"}),
        ParsedToolCall("3", "run_cell", {"cell_index": 3}),
        ParsedToolCall("4", "run_cell", {"cell_index": 4}),
        ParsedToolCall("5", "run_cell", {"cell_index": 5}),
    ]
    execute, deferred = partition_batch(calls)
    assert len([c for c in execute if c.name == "run_cell"]) == 3
    assert deferred == []


def test_normalize_sequential_insert_anchors():
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 2, "direction": "below"}),
        ParsedToolCall("2", "insert_cell", {"index": 2, "direction": "below"}),
        ParsedToolCall("3", "insert_cell", {"index": 2, "direction": "below"}),
    ]
    out = normalize_sequential_insert_anchors(calls)
    anchors = [c.args["index"] for c in out]
    assert anchors == [2, 2, 2]


def test_batch_index_resolution_wrong_llm_pattern():
    """Reproduce r1 bug: 3 inserts at 34,35,36 then edits 35,36,37 → remap to 35,36,37."""
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 34, "direction": "below"}),
        ParsedToolCall("2", "insert_cell", {"index": 35, "direction": "below"}),
        ParsedToolCall("3", "insert_cell", {"index": 36, "direction": "below"}),
        ParsedToolCall("4", "edit_cell_by_index", {"cell_index": 35, "content": "load"}),
        ParsedToolCall("5", "edit_cell_by_index", {"cell_index": 36, "content": "predict"}),
        ParsedToolCall("6", "edit_cell_by_index", {"cell_index": 37, "content": "metrics"}),
        ParsedToolCall("7", "run_cell", {"cell_index": 35}),
        ParsedToolCall("8", "run_cell", {"cell_index": 36}),
        ParsedToolCall("9", "run_cell", {"cell_index": 37}),
    ]
    out = resolve_batch_cell_indices(calls)
    insert_anchors = [c.args["index"] for c in out if c.name == "insert_cell"]
    assert insert_anchors == [34, 34, 34]
    edit_indices = [c.args["cell_index"] for c in out if c.name == "edit_cell_by_index"]
    run_indices = [c.args["cell_index"] for c in out if c.name == "run_cell"]
    assert edit_indices == [35, 36, 37]
    assert run_indices == [35, 36, 37]


def test_expand_multi_cell_from_prompt_builds_full_chain():
    prompt = (
        "create 5 new cells under cell index 2, "
        "print 1,2,3,4,5 in each separate cell, and run those cells"
    )
    out = expand_multi_cell_from_prompt(
        [],
        user_prompt=prompt,
        url="https://example.com/edit",
        tab_id=None,
    )
    assert extract_cell_count_from_prompt(prompt) == 5
    assert parse_multi_cell_contents(prompt, 5) == [
        "print(1)", "print(2)", "print(3)", "print(4)", "print(5)"
    ]
    assert len([c for c in out if c.name == "insert_cell"]) == 5
    assert len([c for c in out if c.name == "edit_cell_by_index"]) == 5
    assert len([c for c in out if c.name == "run_cell"]) == 5
    edit_indices = sorted(c.args["cell_index"] for c in out if c.name == "edit_cell_by_index")
    assert edit_indices == [3, 4, 5, 6, 7]


def test_expand_multi_cell_skips_split_cell_requests():
    prompt = "Split cell 38 into 3 smaller cells"
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 38, "direction": "below"}),
        ParsedToolCall("2", "insert_cell", {"index": 38, "direction": "below"}),
    ]
    out = expand_multi_cell_from_prompt(
        calls,
        user_prompt=prompt,
        url="https://example.com/edit",
        tab_id=None,
    )
    assert out == calls
    assert extract_cell_count_from_prompt(prompt) is None


def test_expand_multi_cell_without_explicit_content_does_not_inject_print():
    prompt = "create 3 new cells under cell index 38"
    out = expand_multi_cell_from_prompt(
        [],
        user_prompt=prompt,
        url="https://example.com/edit",
        tab_id=None,
    )
    assert out == []


def test_enrich_split_cell_does_not_inject_print_placeholders():
    prompt = "Split cell 38 into 3 smaller cells"
    calls = [
        ParsedToolCall("1", "insert_cell", {"index": 38, "direction": "below"}),
        ParsedToolCall("2", "insert_cell", {"index": 38, "direction": "below"}),
        ParsedToolCall("3", "insert_cell", {"index": 38, "direction": "below"}),
    ]
    out = enrich_batch_from_prompt(
        calls,
        user_prompt=prompt,
        url="https://example.com/edit",
        tab_id=None,
    )
    edits = [c for c in out if c.name == "edit_cell_by_index"]
    assert not edits
    assert len([c for c in out if c.name == "insert_cell"]) == 3


def test_user_requests_run_from_prompt_and_tools():
    assert user_requests_run("edit cell 2 and run it", []) is True
    assert user_requests_run("delete cell 5", []) is False
    assert user_requests_run(
        "insert below 2",
        [ParsedToolCall("1", "run_cell", {"cell_index": 3})],
    ) is True


def test_enrich_batch_skips_run_when_not_requested():
    calls = [ParsedToolCall("1", "insert_cell", {"index": 2, "direction": "below"})]
    out = enrich_batch_from_prompt(
        calls,
        user_prompt="insert below cell 2 with print('hi') only",
        url="https://example.com/edit",
        tab_id=None,
    )
    names = [c.name for c in out]
    assert "edit_cell_by_index" in names
    assert "run_cell" not in names


def test_wait_for_cell_run_detects_output_change():
    before = {"cells": [{"index": 1, "input": "print(1)", "output": "", "execution_order": None}]}
    after = {"cells": [{"index": 1, "input": "print(1)", "output": "1\n", "execution_order": 3}]}

    with patch("testing.host.agentic_batch_executor.load_notebook_snapshot") as mock_load:
        mock_load.side_effect = [
            (after, "live"),
        ]
        result = wait_for_cell_run(
            "https://example.com/edit",
            1,
            before,
            timeout=2.0,
            poll_interval=0.01,
        )
    assert result["ok"] is True
    assert result["run_succeeded"] is True
    assert result["output"] == "1\n"


@patch("testing.host.agentic_batch_executor.wait_for_cell_run")
@patch("testing.host.agentic_batch_executor._dispatch_run_cell")
@patch("testing.host.agentic_batch_executor.load_notebook_snapshot")
def test_execute_run_queue_stops_on_cancel(mock_snap, mock_dispatch, mock_wait):
    mock_snap.return_value = ({"cells": []}, "live")
    mock_dispatch.return_value = {"ok": True}
    runs_done = {"n": 0}

    def wait_side_effect(*_a, **_k):
        runs_done["n"] += 1
        return {"ok": True, "run_succeeded": True, "output": "ok"}

    mock_wait.side_effect = wait_side_effect

    def cancel_check():
        return runs_done["n"] >= 1

    completed, _waits, pending = execute_run_queue_sequential(
        [1, 2, 3],
        executed=[],
        registry=MagicMock(),
        url="https://x/edit",
        tab_id=1,
        mode="agentic",
        browser_tool_allowed=lambda *_a, **_k: (True, None),
        cancel_check=cancel_check,
    )
    assert completed == [1]
    assert pending == [2, 3]


def test_build_batch_error_context_includes_succeeded_ops():
    executed = [
        {"tool": "edit_cell_by_index", "cell_index": 3, "dispatched": True, "phase": "write"},
        {"tool": "run_cell", "cell_index": 3, "dispatched": True, "phase": "run_queue"},
        {"tool": "run_cell", "cell_index": 4, "dispatched": True, "phase": "run_queue"},
    ]
    batch_tools = [
        {"tool": "edit_cell_by_index", "args": {"cell_index": 3, "content": "print(1)"}},
        {"tool": "run_cell", "args": {"cell_index": 3}},
        {"tool": "run_cell", "args": {"cell_index": 4}},
    ]
    err_out = "NameError: name 'x' is not defined\n"
    ctx = build_batch_error_context(
        batch_tools,
        executed,
        failed_cell_index=4,
        error_output=err_out,
        pending_run_cells=[5],
        cell_evidence={
            3: {"cell_index": 3, "input": "print(1)", "output": "1\n"},
            4: {"cell_index": 4, "input": "print(x)", "output": err_out},
        },
        run_waits=[
            {"cell_index": 3, "output": "1\n", "run_succeeded": True, "run_verified": True},
            {"cell_index": 4, "output": err_out, "run_succeeded": False, "has_error": True},
        ],
    )
    assert ctx["failed_run"]["cell_index"] == 4
    assert ctx["failed_run"]["error_type"] == "NameError"
    assert ctx["pending_run_cells"] == [5]
    tools = {op["tool"] for op in ctx["succeeded_operations"]}
    assert "edit_cell_by_index" in tools
    assert "run_cell" in tools
    assert all(op.get("cell_index") != 4 for op in ctx["succeeded_operations"])
    run3 = next(op for op in ctx["succeeded_operations"] if op.get("cell_index") == 3)
    assert run3["status"] == "succeeded"
    assert "1" in str(run3.get("output_preview") or "")


def test_build_batch_success_verification_all_cells():
    executed = [
        {"tool": "edit_cell_by_index", "cell_index": 2, "dispatched": True},
        {"tool": "run_cell", "cell_index": 2, "dispatched": True},
    ]
    evidence = {
        2: {"cell_index": 2, "input": "print('hi')", "output": "hi\n"},
    }
    out = build_batch_success_verification(
        [{"tool": "edit_cell_by_index", "args": {"cell_index": 2, "content": "print('hi')"}}],
        evidence,
        executed_results=executed,
        expected_edits={2: "print('hi')"},
        run_waits=[{"cell_index": 2, "output": "hi\n", "run_succeeded": True, "run_verified": True}],
    )
    assert out["all_cells_verified"] is True
    assert len(out["cells"]) >= 2
    ops = {c["operation"] for c in out["cells"]}
    assert "edit_cell_by_index" in ops
    assert "run_cell" in ops
    run_entry = next(c for c in out["cells"] if c["operation"] == "run_cell")
    assert run_entry["run_succeeded"] is True
    assert "hi" in str(run_entry.get("output_preview") or "")


def test_finalize_attaches_batch_error_context():
    registry = MagicMock()
    registry.call = lambda name, args: {
        "ok": True,
        "cell_index": args.get("cell_index"),
        "input": "print(bad)",
        "output": "NameError: bad",
        "type": "code",
    }
    base = verify_workflow_batch(
        before_data={"cells": []},
        after_data={"cells": []},
        executed=[
            {"tool": "edit_cell_by_index", "cell_index": 5, "dispatched": True},
            {"tool": "run_cell", "cell_index": 5, "dispatched": True},
        ],
        expected_edits={5: "print(bad)"},
        run_cell_indices=[5],
        run_waits=[{
            "ok": True,
            "output": "NameError: bad",
            "run_succeeded": False,
            "has_error": True,
            "error_type": "NameError",
            "cell_index": 5,
            "run_verified": True,
        }],
    )
    out = finalize_tool_queue_verification(
        base,
        registry=registry,
        url="https://x/edit",
        expected_edits={5: "print(bad)"},
        run_requested=[5, 6],
        run_completed=[5],
        run_pending=[6],
        batch_tools=[
            {"tool": "edit_cell_by_index", "args": {"cell_index": 5, "content": "print(bad)"}},
            {"tool": "run_cell", "args": {"cell_index": 5}},
        ],
    )
    ctx = out.get("batch_error_context") or {}
    assert ctx.get("failed_run", {}).get("cell_index") == 5
    assert any(op.get("tool") == "edit_cell_by_index" for op in ctx.get("succeeded_operations") or [])
    assert out["close_react_loop"] is False
    assert out.get("goal_verified") is not True


def test_finalize_delete_insert_write_only_closes_queue():
    before = {
        "cells": [
            {"index": 1, "input": "a", "type": "code"},
            {"index": 2, "input": "b", "type": "code"},
        ]
    }
    after = {
        "cells": [
            {"index": 1, "input": "a", "type": "code"},
            {"index": 2, "input": "", "type": "code"},
        ]
    }
    executed = [
        {"tool": "delete_by_index", "dispatched": True, "cell_index": 2},
        {"tool": "insert_cell", "dispatched": True},
    ]
    base = verify_workflow_batch(
        before_data=before,
        after_data=after,
        executed=executed,
        expected_edits={},
        run_cell_indices=[],
        run_waits=[],
    )
    assert base["verified"] is True
    out = finalize_tool_queue_verification(
        base,
        registry=None,
        url="https://x/edit",
        expected_edits={},
        run_requested=[],
        run_completed=[],
        run_pending=[],
        user_prompt="delete cell 2 and create new cell under cell 1",
        batch_tools=[
            {"tool": "delete_by_index", "args": {"cell_index": 2}},
            {"tool": "insert_cell", "args": {"index": 1, "direction": "below"}},
        ],
    )
    assert out.get("tool_queue_complete") is True
    assert out.get("goal_verified") is True
    assert out.get("close_react_loop") is True


def test_finalize_attaches_batch_success_verification():
    registry = MagicMock()
    registry.call = lambda name, args: {
        "ok": True,
        "cell_index": args.get("cell_index"),
        "input": "print(1)",
        "output": "1\n",
        "type": "code",
    }
    base = verify_workflow_batch(
        before_data={"cells": [{"index": 1, "input": "old", "type": "code"}]},
        after_data={"cells": [{"index": 1, "input": "print(1)", "type": "code", "output": "1\n"}]},
        executed=[
            {"tool": "edit_cell_by_index", "cell_index": 1, "dispatched": True},
            {"tool": "run_cell", "cell_index": 1, "dispatched": True},
        ],
        expected_edits={1: "print(1)"},
        run_cell_indices=[1],
        run_waits=[{"ok": True, "output": "1\n", "run_succeeded": True, "run_verified": True, "cell_index": 1}],
    )
    assert base["verified"] is True
    out = finalize_tool_queue_verification(
        base,
        registry=registry,
        url="https://x/edit",
        expected_edits={1: "print(1)"},
        run_requested=[1],
        run_completed=[1],
        run_pending=[],
        user_prompt="edit and run cell 1",
        run_waits=[{"ok": True, "output": "1\n", "run_succeeded": True, "run_verified": True, "cell_index": 1}],
        batch_tools=[
            {"tool": "edit_cell_by_index", "args": {"cell_index": 1, "content": "print(1)"}},
            {"tool": "run_cell", "args": {"cell_index": 1}},
        ],
    )
    success = out.get("batch_success_verification") or {}
    assert success.get("all_cells_verified") is True
    assert len(success.get("cells") or []) >= 2


def test_workflow_needs_followup_false_when_fire_and_forget():
    v = build_fire_and_forget_verification(
        executed=[{"tool": "edit_cell_by_index", "dispatched": True, "cell_index": 1}],
        run_dispatched=[1],
        run_pending=[],
        expected_edits={1: "print(1)"},
    )
    assert v["fire_and_forget"] is True
    assert workflow_needs_llm_followup(v) is False


def test_build_fire_and_forget_user_summary():
    v = build_fire_and_forget_verification(
        executed=[
            {"tool": "edit_cell_by_index", "dispatched": True, "cell_index": 10},
            {"tool": "run_cell", "dispatched": True, "cell_index": 10},
        ],
        run_dispatched=[10],
        run_pending=[],
        expected_edits={10: "print(1)"},
    )
    summary = build_fire_and_forget_user_summary(v)
    assert "fire-and-forget" in summary.lower()
    assert "edit_cell_by_index(10)" in summary
    assert "run_cell(10)" in summary


def test_execute_run_queue_dispatch_only_no_wait():
    registry = MagicMock()
    registry.call.return_value = {"ok": True}
    executed: list = []
    with patch("testing.host.agentic_batch_executor.wait_for_cell_run") as mock_wait:
        dispatched, pending = execute_run_queue_dispatch_only(
            [5, 6],
            executed=executed,
            registry=registry,
            url="https://x/edit",
            tab_id=1,
            mode="agentic",
            browser_tool_allowed=lambda _m, _t: (True, None),
            inter_delay=0.0,
        )
    assert dispatched == [5, 6]
    assert pending == []
    assert len(executed) == 2
    assert all(row.get("dispatched") for row in executed)
    mock_wait.assert_not_called()


@patch("testing.host.agentic_batch_executor.AGENTIC_FIRE_AND_FORGET", True)
@patch("testing.host.agentic_batch_executor.wait_for_snapshot_change")
@patch("testing.host.agentic_batch_executor.wait_for_cell_run")
@patch("testing.host.agentic_batch_executor.load_notebook_snapshot")
def test_execute_agentic_batch_fire_and_forget_skips_waits(mock_snap, mock_wait, mock_snap_change):
    mock_snap.return_value = ({"cells": []}, "live")
    registry = MagicMock()
    registry.call.return_value = {"ok": True, "dispatched": True, "phase": "dispatched"}
    tool_calls = [
        {
            "id": "tc1",
            "function": {
                "name": "edit_cell_by_index",
                "arguments": json.dumps({"cell_index": 3, "content": "print(1)", "url": "https://x/edit"}),
            },
        },
        {
            "id": "tc2",
            "function": {
                "name": "run_cell",
                "arguments": json.dumps({"cell_index": 3, "url": "https://x/edit"}),
            },
        },
    ]
    from testing.host.agentic_batch_executor import execute_agentic_batch

    out = execute_agentic_batch(
        tool_calls,
        user_prompt="edit cell 3 and run",
        url="https://x/edit",
        tab_id=1,
        registry=registry,
        browser_tool_allowed=lambda _m, _t: (True, None),
        mode="agentic",
        inter_delay=0.0,
    )
    assert out.get("fire_and_forget") is True
    assert workflow_needs_llm_followup(out) is False
    mock_snap_change.assert_not_called()
    mock_wait.assert_not_called()

