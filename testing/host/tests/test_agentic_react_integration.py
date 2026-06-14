"""Integration: edit+run completes with 1 ReAct LLM call + 1 tool_final call."""

import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import testing.host.streaming as streaming
from testing.host.agentic_mode import set_dashboard_agentic_enabled


class _FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self):
        return {"content": self.content, "tool_calls": self.tool_calls}


class _FakeChoice:
    def __init__(self, message):
        self.message = message

    def model_dump(self):
        return {"message": self.message.model_dump()}


class _FakeResponse:
    def __init__(self, content="", tool_calls=None):
        self.choices = [_FakeChoice(_FakeMessage(content, tool_calls))]

    def model_dump(self):
        return {"choices": [c.model_dump() for c in self.choices]}


class FakeCompletions:
    def __init__(self, batch_text: str):
        self.calls: list[dict] = []
        self._n = 0
        self._batch_text = batch_text

    def create(self, **kwargs):
        self.calls.append({"messages_len": len(kwargs.get("messages") or [])})
        self._n += 1
        if self._n == 1:
            return _FakeResponse(content=self._batch_text)
        return _FakeResponse(content="Done: cell 10 updated and ran successfully.")


class FakeClient:
    def __init__(self, batch_text: str):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(batch_text)


def test_edit_run_one_react_round_plus_final():
    os.environ["AGENTIC_TEXT_TOOLS"] = "1"
    set_dashboard_agentic_enabled(True)

    batch = (
        '<agent_tool_batch>[{"tool":"edit_cell_by_index","args":{"cell_index":10,"content":"print(1)"}},'
        '{"tool":"run_cell","args":{"cell_index":10}}]</agent_tool_batch>'
    )
    fake = FakeClient(batch)
    streaming._LLM_CLIENT = fake
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    prompt = "Edit cell 10 to print(1) and run it"

    verification = {
        "verified": True,
        "batch_executed": True,
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "run_queue_complete": True,
        "await_llm_summary": True,
        "pending_run_cells": [],
        "deferred_tool_calls": [],
        "needs_fix": False,
        "runs_executed": [10],
        "executed": [
            {"tool": "edit_cell_by_index", "cell_index": 10},
            {"tool": "run_cell", "cell_index": 10},
        ],
    }

    patches = [
        patch("testing.host.streaming.send_msg", lambda *a, **k: None),
        patch("testing.host.streaming.memory_store.append", lambda *a, **k: None),
        patch("testing.host.streaming._wait_for_request_slot", lambda *a, **k: True),
        patch("testing.host.streaming._check_token_limits", lambda: (True, {})),
        patch("testing.host.streaming._record_llm_usage", lambda *a, **k: None),
        patch("testing.host.streaming._finalize_request_attempt", lambda *a, **k: None),
        patch("testing.host.streaming._record_request_attempt", lambda *a, **k: None),
        patch("testing.host.agentic_tool_chain.build_direct_edit_from_prompt", lambda *a, **k: None),
        patch("testing.host.notebook_query.prefetch_notebook_queries", lambda **k: ("", [])),
        patch("testing.host.agentic_batch_executor.execute_agentic_batch", lambda *a, **k: verification),
    ]
    for p in patches:
        p.start()
    try:
        streaming._run_streaming_chat(
            url,
            prompt,
            tab_id=1,
            session_id="t",
            history=[],
            context="cells 1-25",
            mode="agentic",
            explicit_mode="agentic",
            context_meta={"history_key": url, "snapshot_url": url, "active_key": "1"},
        )
    finally:
        for p in patches:
            p.stop()

    assert len(fake.chat.completions.calls) == 2, fake.chat.completions.calls
    assert fake.chat.completions.calls[0]["messages_len"] >= 2
