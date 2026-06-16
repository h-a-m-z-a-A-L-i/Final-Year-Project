"""Integration: multi-round fire-and-forget ReAct without verification loops."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

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


def _native_tool_call(name: str, args: dict, call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


class FakeCompletions:
    def __init__(self, responses: list[_FakeResponse]):
        self.calls: list[dict] = []
        self._responses = list(responses)
        self._n = 0

    def create(self, **kwargs):
        self.calls.append({"messages_len": len(kwargs.get("messages") or [])})
        idx = min(self._n, len(self._responses) - 1)
        self._n += 1
        return self._responses[idx]


class FakeClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(responses)


def _list_cells_verification():
    return {
        "verified": True,
        "batch_executed": True,
        "fire_and_forget": True,
        "tool_queue_status": "dispatched",
        "tool_queue_complete": True,
        "run_queue_complete": True,
        "executed": [{"tool": "notebook_list_cells", "dispatched": True, "phase": "read"}],
        "read_results": [{"tool": "notebook_list_cells", "result": {"ok": True, "cells": []}}],
    }


def _write_batch_verification():
    return {
        "verified": True,
        "batch_executed": True,
        "fire_and_forget": True,
        "tool_queue_status": "dispatched",
        "tool_queue_complete": True,
        "run_queue_complete": True,
        "executed": [
            {"tool": "edit_cell_by_index", "cell_index": 10, "dispatched": True},
            {"tool": "run_cell", "cell_index": 10, "dispatched": True},
        ],
        "runs_dispatched": [10],
    }


def _common_patches(*, max_rounds: int, emitted: list[str], batch_mock):
    def _capture_delta(*_a, delta="", **_k):
        emitted.append(delta)

    return [
        patch.dict(os.environ, {"AGENTIC_TEXT_TOOLS": "0", "AGENTIC_MAX_TOOL_ROUNDS": str(max_rounds)}),
        patch("testing.host.streaming.send_msg", lambda *a, **k: None),
        patch("testing.host.streaming.memory_store.append", lambda *a, **k: None),
        patch("testing.host.streaming._wait_for_request_slot", lambda *a, **k: True),
        patch("testing.host.streaming._check_token_limits", lambda: (True, {})),
        patch("testing.host.streaming._record_llm_usage", lambda *a, **k: None),
        patch("testing.host.streaming._finalize_request_attempt", lambda *a, **k: None),
        patch("testing.host.streaming._record_request_attempt", lambda *a, **k: None),
        patch("testing.host.streaming._begin_llm_request", lambda _stop: "attempt"),
        patch("testing.host.agentic_tool_chain.build_direct_edit_from_prompt", lambda *a, **k: None),
        patch("testing.host.notebook_query.prefetch_notebook_queries", lambda **k: ("", [])),
        patch("testing.host.streaming.AGENTIC_FIRE_AND_FORGET", True),
        patch("testing.host.streaming.AGENTIC_MAX_TOOL_ROUNDS", max_rounds),
        patch("testing.host.streaming.AGENTIC_MAX_QUERY_ROUNDS", 1),
        patch("testing.host.streaming.AGENTIC_MANDATORY_TWO_PHASE", True),
        patch("testing.host.streaming._emit_stream_delta", _capture_delta),
        batch_mock,
    ]


def _run_stream_with_patches(fake_client, *, max_rounds: int = 2):
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    prompt = "Edit cell 10 to print(1) and run it"
    emitted: list[str] = []
    batch_side_effect = [_list_cells_verification(), _write_batch_verification()]
    patches = _common_patches(
        max_rounds=max_rounds,
        emitted=emitted,
        batch_mock=patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            side_effect=batch_side_effect,
        ),
    )
    streaming._LLM_CLIENT = fake_client
    set_dashboard_agentic_enabled(True)
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
    return fake_client.chat.completions.calls, "".join(emitted)


def test_multi_round_query_then_write_stops_on_prose():
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    responses = [
        _FakeResponse(
            tool_calls=[
                _native_tool_call("notebook_list_cells", {"url": url}, "c1"),
            ],
        ),
        _FakeResponse(
            tool_calls=[
                _native_tool_call(
                    "edit_cell_by_index",
                    {"url": url, "cell_index": 10, "content": "print(1)"},
                    "c2",
                ),
                _native_tool_call("run_cell", {"url": url, "cell_index": 10}, "c3"),
            ],
        ),
        _FakeResponse(content="Done: cell 10 updated and dispatched."),
    ]
    calls, emitted = _run_stream_with_patches(FakeClient(responses), max_rounds=2)

    assert len(calls) == 2, calls
    assert "notebook_list_cells" in emitted or "fire-and-forget" in emitted.lower()
    assert "edit_cell_by_index" in emitted
    assert "run_cell" in emitted
    assert "Tools ran but no summary" not in emitted


def test_single_round_write_batch_requires_two_phases():
    """Mandatory two-phase: writes on call 1 are stripped; call 2 implements."""
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    responses = [
        _FakeResponse(
            tool_calls=[
                _native_tool_call(
                    "edit_cell_by_index",
                    {"url": url, "cell_index": 10, "content": "print(1)"},
                    "c1",
                ),
                _native_tool_call("run_cell", {"url": url, "cell_index": 10}, "c2"),
            ],
        ),
        _FakeResponse(
            tool_calls=[
                _native_tool_call(
                    "edit_cell_by_index",
                    {"url": url, "cell_index": 10, "content": "print(1)"},
                    "c3",
                ),
                _native_tool_call("run_cell", {"url": url, "cell_index": 10}, "c4"),
            ],
        ),
        _FakeResponse(content="Updated cell 10."),
    ]
    emitted: list[str] = []
    batch_calls: list[list] = []

    def _capture_batch(tool_calls, **kwargs):
        batch_calls.append(list(tool_calls))
        if kwargs.get("force_implementation"):
            return _write_batch_verification()
        first_name = tool_calls[0]["function"]["name"] if tool_calls else ""
        if first_name == "notebook_get_cell":
            return _list_cells_verification()
        return _write_batch_verification()

    patches = _common_patches(
        max_rounds=2,
        emitted=emitted,
        batch_mock=patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            side_effect=_capture_batch,
        ),
    )
    fake = FakeClient(responses)
    streaming._LLM_CLIENT = fake
    set_dashboard_agentic_enabled(True)
    for p in patches:
        p.start()
    try:
        streaming._run_streaming_chat(
            url,
            "Edit cell 10 to print(1) and run it",
            tab_id=1,
            session_id="t2",
            history=[],
            context="cells 1-25",
            mode="agentic",
            explicit_mode="agentic",
            context_meta={"history_key": url, "snapshot_url": url, "active_key": "1"},
        )
    finally:
        for p in patches:
            p.stop()

    text = "".join(emitted)
    assert len(fake.chat.completions.calls) == 2
    assert batch_calls
    round0_names = [tc["function"]["name"] for tc in batch_calls[0]]
    assert round0_names == ["notebook_get_cell"]
    assert "fire-and-forget" in text.lower()
    assert "edit_cell_by_index" in text


def test_does_not_continue_past_max_rounds():
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    always_tools = _FakeResponse(
        tool_calls=[_native_tool_call("notebook_list_cells", {"url": url}, "cx")],
    )
    emitted: list[str] = []

    def _capture_batch(tool_calls, **kwargs):
        if kwargs.get("force_implementation"):
            return _write_batch_verification()
        return _list_cells_verification()

    patches = _common_patches(
        max_rounds=2,
        emitted=emitted,
        batch_mock=patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            side_effect=_capture_batch,
        ),
    )
    fake = FakeClient([always_tools] * 6)
    streaming._LLM_CLIENT = fake
    set_dashboard_agentic_enabled(True)
    for p in patches:
        p.start()
    try:
        streaming._run_streaming_chat(
            url,
            "Edit cell 10 to print(1) and run it",
            tab_id=1,
            session_id="t3",
            history=[],
            context="cells 1-25",
            mode="agentic",
            explicit_mode="agentic",
            context_meta={"history_key": url, "snapshot_url": url, "active_key": "1"},
        )
    finally:
        for p in patches:
            p.stop()

    text = "".join(emitted)
    assert len(fake.chat.completions.calls) == 2, fake.chat.completions.calls
    assert "fire-and-forget" in text.lower()
    assert "notebook_list_cells" in text
    assert "edit_cell_by_index" in text


def test_never_makes_third_api_call():
    """Hard cap: max_tool_rounds=2 means at most 2 LLM calls even if model keeps querying."""
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    always_query = _FakeResponse(
        tool_calls=[_native_tool_call("notebook_list_cells", {"url": url}, "cx")],
    )
    emitted: list[str] = []
    patches = _common_patches(
        max_rounds=2,
        emitted=emitted,
        batch_mock=patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            side_effect=lambda *_a, **_k: _list_cells_verification(),
        ),
    )
    fake = FakeClient([always_query] * 5)
    streaming._LLM_CLIENT = fake
    set_dashboard_agentic_enabled(True)
    for p in patches:
        p.start()
    try:
        streaming._run_streaming_chat(
            url,
            ML_PROMPT,
            tab_id=1,
            session_id="t5",
            history=[],
            context="cells 1-30",
            mode="agentic",
            explicit_mode="agentic",
            context_meta={"history_key": url, "snapshot_url": url, "active_key": "1"},
        )
    finally:
        for p in patches:
            p.stop()

    assert len(fake.chat.completions.calls) <= 2


ML_PROMPT = (
    "import (/kaggle/input/datasets/codekey/zameen-com2026-16-5/zameen_master_dataset.csv) "
    "dataset and make a simple linear regression model, then make new cell and do the "
    "predictions, then make a new cell and print the models performance"
)


def test_ml_query_then_force_write_on_second_round():
    """After one list_cells round, host forces insert/edit/run when LLM repeats queries."""
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    responses = [
        _FakeResponse(
            tool_calls=[_native_tool_call("notebook_list_cells", {"url": url}, "c1")],
        ),
        _FakeResponse(
            tool_calls=[_native_tool_call("notebook_list_cells", {"url": url}, "c2")],
        ),
        _FakeResponse(content="ML workflow cells dispatched."),
    ]
    emitted: list[str] = []
    force_flags: list[bool] = []

    def _capture_batch(tool_calls, **kwargs):
        force_flags.append(bool(kwargs.get("force_implementation")))
        if kwargs.get("force_implementation"):
            return {
                "verified": True,
                "batch_executed": True,
                "fire_and_forget": True,
                "tool_queue_status": "dispatched",
                "tool_queue_complete": True,
                "run_queue_complete": True,
                "executed": [
                    {"tool": "insert_cell", "dispatched": True, "cell_index": 31},
                    {"tool": "edit_cell_by_index", "dispatched": True, "cell_index": 31},
                    {"tool": "run_cell", "dispatched": True, "cell_index": 31},
                ],
            }
        return _list_cells_verification()

    patches = _common_patches(
        max_rounds=2,
        emitted=emitted,
        batch_mock=patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            side_effect=_capture_batch,
        ),
    )
    fake = FakeClient(responses)
    streaming._LLM_CLIENT = fake
    set_dashboard_agentic_enabled(True)
    for p in patches:
        p.start()
    try:
        streaming._run_streaming_chat(
            url,
            ML_PROMPT,
            tab_id=1,
            session_id="tml",
            history=[],
            context="cells 1-30",
            mode="agentic",
            explicit_mode="agentic",
            context_meta={"history_key": url, "snapshot_url": url, "active_key": "1"},
        )
    finally:
        for p in patches:
            p.stop()

    text = "".join(emitted)
    assert len(fake.chat.completions.calls) == 2
    assert force_flags == [False, True]
    assert "insert_cell" in text
    assert "edit_cell_by_index" in text
    assert "run_cell" in text


def test_returns_non_empty_summary_on_max_rounds():
    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    responses = [
        _FakeResponse(
            tool_calls=[_native_tool_call("notebook_list_cells", {"url": url}, "c1")],
        ),
    ] * 2
    emitted: list[str] = []
    patches = _common_patches(
        max_rounds=2,
        emitted=emitted,
        batch_mock=patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            side_effect=lambda *_a, **_k: _list_cells_verification(),
        ),
    )
    fake = FakeClient(responses)
    streaming._LLM_CLIENT = fake
    set_dashboard_agentic_enabled(True)
    for p in patches:
        p.start()
    try:
        streaming._run_streaming_chat(
            url,
            "Edit cell 10 to print(1) and run it",
            tab_id=1,
            session_id="t4",
            history=[],
            context="cells 1-25",
            mode="agentic",
            explicit_mode="agentic",
            context_meta={"history_key": url, "snapshot_url": url, "active_key": "1"},
        )
    finally:
        for p in patches:
            p.stop()

    text = "".join(emitted)
    assert text.strip()
    assert "fire-and-forget" in text.lower()
    assert "Tools ran but no summary" not in text


def test_bulk_delete_round0_list_round1_force_implementation():
    """After round 0 list_cells, round 1 must force delete batch when LLM repeats query."""
    from testing.host.agentic_batch_executor import execute_agentic_batch
    from testing.host.agentic_mode import browser_tool_allowed

    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    tab_id = 2015941739
    prompt = "remove cells 2,4,,7,3,6,5"
    responses = [
        _FakeResponse(
            tool_calls=[
                _native_tool_call("notebook_list_cells", {"url": url, "tab_id": tab_id}, "c1"),
            ],
        ),
        _FakeResponse(
            tool_calls=[
                _native_tool_call("notebook_list_cells", {"url": url, "tab_id": tab_id}, "c2"),
            ],
        ),
    ]
    emitted: list[str] = []
    force_flags: list[bool] = []
    delete_indices: list[int] = []

    def _smart_batch(tool_calls, **kwargs):
        force_flags.append(bool(kwargs.get("force_implementation")))
        if kwargs.get("force_implementation"):
            reg = MagicMock()
            reg.call.return_value = {"ok": True, "dispatched": True, "phase": "dispatched"}
            out = execute_agentic_batch(
                tool_calls,
                user_prompt=kwargs["user_prompt"],
                url=kwargs["url"],
                tab_id=kwargs["tab_id"],
                registry=reg,
                browser_tool_allowed=browser_tool_allowed,
                mode=kwargs.get("mode", "agentic"),
                force_implementation=True,
                trace_round=kwargs.get("trace_round"),
            )
            for item in out.get("executed") or []:
                if item.get("tool") == "delete_by_index":
                    delete_indices.append(int(item["cell_index"]))
            return out
        return _list_cells_verification()

    patches = _common_patches(
        max_rounds=2,
        emitted=emitted,
        batch_mock=patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            side_effect=_smart_batch,
        ),
    )
    fake = FakeClient(responses)
    streaming._LLM_CLIENT = fake
    set_dashboard_agentic_enabled(True)
    for p in patches:
        p.start()
    try:
        streaming._run_streaming_chat(
            url,
            prompt,
            tab_id=tab_id,
            session_id="bulk-del",
            history=[],
            context="cells 1-45",
            mode="agentic",
            explicit_mode="agentic",
            context_meta={"history_key": url, "snapshot_url": url, "active_key": "1"},
        )
    finally:
        for p in patches:
            p.stop()

    assert len(fake.chat.completions.calls) == 2
    assert force_flags == [False, True]
    assert delete_indices == [7, 6, 5, 4, 3, 2]
    text = "".join(emitted)
    assert "delete_by_index" in text
