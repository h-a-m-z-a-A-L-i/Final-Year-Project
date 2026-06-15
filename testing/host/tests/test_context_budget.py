import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.context_budget import (
    compress_react_verification_history,
    ensure_tool_call_message_pairs,
    estimate_messages_tokens,
    fit_messages_to_budget,
    fit_react_messages_to_budget,
    messages_for_api,
    trim_history_for_api,
)
from testing.host.agentic_verification import (
    VERIFICATION_MARKER,
    append_batch_verification_message,
    append_native_batch_tool_results,
)


def test_trim_history_for_api_caps_count_and_chars():
    history = [{"role": "user", "content": "x" * 5000} for _ in range(20)]
    trimmed = trim_history_for_api(history)
    assert len(trimmed) <= 10
    assert all(len(m["content"]) <= 1000 + 50 for m in trimmed)


def test_fit_messages_drops_old_turns():
    messages = [{"role": "system", "content": "sys " * 100}]
    for i in range(15):
        messages.append({"role": "user", "content": f"old {i} " * 200})
        messages.append({"role": "assistant", "content": f"reply {i} " * 200})
    messages.append({"role": "user", "content": "current question"})
    fitted = fit_messages_to_budget(messages, max_tokens=800)
    assert fitted[-1]["content"] == "current question"
    assert estimate_messages_tokens(fitted) <= 800


def test_messages_for_api_strips_internal_react_keys():
    raw = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task", "_react_original_user": True},
        {"role": "assistant", "content": "ok", "_react_tool_batch": True, "tool_calls": [{"id": "1"}]},
        {"role": "user", "content": "state", "_react_agent_state": True},
    ]
    api = messages_for_api(raw)
    assert all("_react_" not in str(m.keys()) for m in api)
    assert api[1] == {"role": "user", "content": "task"}
    assert api[2]["tool_calls"] == [{"id": "1"}]


def test_compress_verification_history_keeps_latest_five():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task", "_react_original_user": True}]
    for i in range(12):
        v = {
            "verified": True,
            "executed": [{"tool": "edit_cell_by_index", "cell_index": i + 1}],
            "queue_cell_evidence": {"cells": [{"cell_index": i + 1, "input": "x=1", "output": "ok"}]},
        }
        messages.append({"role": "assistant", "content": f"batch {i}", "_react_tool_batch": True})
        append_batch_verification_message(messages, v, round_idx=i)

    compressed = compress_react_verification_history(messages, keep_latest=5)
    ver_count = sum(
        1 for m in compressed
        if m.get("_react_verification") and not m.get("_react_verification_summary")
    )
    summary_count = sum(1 for m in compressed if m.get("_react_verification_summary"))
    assert summary_count == 1
    assert ver_count == 5
    summary_msg = next(m for m in compressed if m.get("_react_verification_summary"))
    assert "VERIFICATION SUMMARY" in summary_msg["content"]
    assert "completed_actions_count: 7" in summary_msg["content"]


def test_fit_react_sublinear_token_growth_with_compression():
    messages = [{"role": "system", "content": "sys " * 200}, {"role": "user", "content": "task", "_react_original_user": True}]
    for i in range(40):
        v = {
            "verified": True,
            "executed": [{"tool": "edit_cell_by_index", "cell_index": (i % 10) + 1}],
            "queue_cell_evidence": {
                "cells": [{"cell_index": (i % 10) + 1, "input": "x=1\n" * 5, "output": "shape (100, 10)"}],
            },
        }
        messages.append({"role": "assistant", "content": "tool " * 30, "_react_tool_batch": True})
        append_batch_verification_message(messages, v, round_idx=i)

    fitted_20, _ = fit_react_messages_to_budget(messages[:2 + 20 * 2], max_tokens=5500, original_user_prompt="task")
    fitted_40, _ = fit_react_messages_to_budget(messages, max_tokens=5500, original_user_prompt="task")
    t20 = estimate_messages_tokens(fitted_20)
    t40 = estimate_messages_tokens(fitted_40)
    assert t40 < t20 * 1.5


def test_ensure_tool_call_message_pairs_fills_missing_tool_responses():
    raw = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a1", "function": {"name": "insert_cell", "arguments": "{}"}},
            {"id": "a2", "function": {"name": "edit_cell_by_index", "arguments": "{}"}},
        ]},
        {"role": "user", "content": "verification"},
    ]
    repaired = ensure_tool_call_message_pairs(raw)
    tool_msgs = [m for m in repaired if m.get("role") == "tool"]
    assert {m["tool_call_id"] for m in tool_msgs} == {"a1", "a2"}


def test_fit_react_keeps_tool_responses_with_assistant_batch():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task", "_react_original_user": True}]
    for i in range(8):
        batch_tool_calls = [
            {"id": f"tc_{i}_1", "function": {"name": "insert_cell", "arguments": "{}"}},
            {"id": f"tc_{i}_2", "function": {"name": "edit_cell_by_index", "arguments": "{}"}},
            {"id": f"tc_{i}_3", "function": {"name": "run_cell", "arguments": "{}"}},
        ]
        v = {
            "verified": i == 7,
            "batch_executed": True,
            "executed": [{"tool": "insert_cell", "cell_index": 10 + i}],
            "tool_queue_complete": i == 7,
        }
        messages.append({"role": "assistant", "content": "", "_react_tool_batch": True, "tool_calls": batch_tool_calls})
        append_native_batch_tool_results(messages, batch_tool_calls, v, round_idx=i)
        append_batch_verification_message(messages, v, round_idx=i)

    fitted, _ = fit_react_messages_to_budget(messages, max_tokens=2500, original_user_prompt="task")
    api = messages_for_api(fitted)
    ai = next(
        i
        for i, m in enumerate(api)
        if m.get("role") == "assistant"
        and m.get("tool_calls")
        and any(str(tc.get("id", "")).startswith("tc_7_") for tc in m["tool_calls"])
    )
    ids = {tc["id"] for tc in api[ai]["tool_calls"]}
    tool_ids: set[str] = set()
    for m in api[ai + 1 :]:
        if m.get("role") == "tool" and m.get("tool_call_id"):
            tool_ids.add(str(m["tool_call_id"]))
        elif m.get("role") != "tool":
            break
    assert ids <= tool_ids
