#!/usr/bin/env python3
"""
Reproduce LLM Call #2 message array for a failing multi-step agentic workflow.

Sets REACT_DEBUG_CALL2=1 and drives streaming._run_streaming_chat with mocked LLM
and execute_agentic_batch so Round 1 completes and Round 2 hits instrumented logging.

Usage:
  python testing/host/scripts/debug_react_call2_messages.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Must be set before config/streaming import side effects we rely on for debug.
os.environ["REACT_DEBUG_CALL2"] = "1"
os.environ["ENABLE_TPM_PREFLIGHT"] = "0"
os.environ["AGENTIC_TEXT_TOOLS"] = "1"
# Force budget pressure similar to full-notebook agentic turns (static cache path).
os.environ.setdefault("CEREBRAS_STATIC_NOTEBOOK_CACHE", "1")
os.environ.setdefault("MAX_INPUT_TOKENS", "5500")

ORIGINAL_USER = (
    "Edit cell 10 to print('hello world') and run it to verify the output shows hello world."
)

URL = "https://www.kaggle.com/code/codekey/testing-ol/edit"

ROUND1_BATCH = (
    "<agent_tool_batch>\n"
    + json.dumps(
        [
            {
                "tool": "edit_cell_by_index",
                "args": {"cell_index": 10, "content": "print('hello world')", "url": URL},
            },
            {"tool": "run_cell", "args": {"cell_index": 10, "url": URL}},
        ],
        indent=2,
    )
    + "\n</agent_tool_batch>"
)


def _large_verification_payload() -> dict:
    """Realistic post-batch verification JSON sized to stress fit_messages_to_budget."""
    cells = []
    for i in range(1, 26):
        cells.append(
            {
                "cell_index": i,
                "type": "code",
                "input": f"# cell {i}\n" + ("import pandas as pd\n" * 8) + f"print({i})",
                "output": ("hello world\n" * 40 if i == 10 else f"out {i}\n" * 12),
                "execution_order": i if i <= 10 else None,
            }
        )
    return {
        "verified": True,
        "batch_executed": True,
        "cell_index": 10,
        "cell_output": "hello world\n",
        "tool_queue_status": "complete",
        "tool_queue_complete": True,
        "run_queue_complete": True,
        "await_llm_summary": True,
        "close_react_loop": True,
        "runs_requested": [10],
        "runs_executed": [10],
        "queue_cell_evidence": {"count": len(cells), "cells": cells},
        "target_cells": cells,
        "user_response_gate": (
            "Tool queue complete. Verify every target cell in queue_cell_evidence "
            "(input + output). Write a final Summary — no further tools."
        ),
        "tool_queue": {
            "run_requested": [10],
            "run_completed": [10],
            "run_pending": [],
            "delay_sec": 0.35,
        },
    }


class _FakeMessage:
    def __init__(self, content: str = "", tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self):
        return {"content": self.content, "tool_calls": self.tool_calls}


class _FakeChoice:
    def __init__(self, message: _FakeMessage):
        self.message = message

    def model_dump(self):
        return {"message": self.message.model_dump()}


class _FakeResponse:
    def __init__(self, content: str = "", tool_calls: list | None = None):
        self.choices = [_FakeChoice(_FakeMessage(content, tool_calls))]

    def model_dump(self):
        return {"choices": [c.model_dump() for c in self.choices]}


class FakeCompletions:
    def __init__(self):
        self.call_log: list[dict] = []
        self._n = 0

    def create(self, **kwargs):
        self.call_log.append({"n": self._n, "message_count": len(kwargs.get("messages") or [])})
        self._n += 1
        if self._n == 1:
            return _FakeResponse(content=ROUND1_BATCH)
        return _FakeResponse(content="Summary: cell 10 prints hello world.")


class FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions()


def main() -> int:
    import testing.host.streaming as streaming
    from testing.host.agentic_mode import set_dashboard_agentic_enabled

    set_dashboard_agentic_enabled(True)
    fake = FakeClient()
    streaming._LLM_CLIENT = fake

    notebook_context = (
        "Notebook testing-ol has 25 code cells.\n"
        + "\n".join(f"Cell {i}: print placeholder" for i in range(1, 26))
        + "\n"
        + ("# static baseline padding\n" * 200)
    )

    context_meta = {
        "history_key": URL,
        "snapshot_url": URL,
        "active_key": "999",
        "static_cache": True,
        "coverage": "full",
        "cell_index": 10,
        "cache_session_id": "debug-call2",
    }

    patches = [
        patch("testing.host.streaming.send_msg", lambda *a, **k: None),
        patch("testing.host.streaming.memory_store.append", lambda *a, **k: None),
        patch("testing.host.streaming._wait_for_request_slot", lambda *a, **k: True),
        patch("testing.host.streaming._check_token_limits", lambda: (True, {})),
        patch("testing.host.streaming._record_llm_usage", lambda *a, **k: None),
        patch("testing.host.streaming._finalize_request_attempt", lambda *a, **k: None),
        patch("testing.host.streaming._record_request_attempt", lambda *a, **k: None),
        patch(
            "testing.host.agentic_tool_chain.build_direct_edit_from_prompt",
            lambda *a, **k: None,
        ),
        patch(
            "testing.host.notebook_query.prefetch_notebook_queries",
            lambda **k: ("[prefetch notebook_get_cell cell 10 output preview...]", []),
        ),
        patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            lambda *a, **k: _large_verification_payload(),
        ),
    ]

    for p in patches:
        p.start()

    try:
        streaming._run_streaming_chat(
            URL,
            ORIGINAL_USER,
            tab_id=999,
            session_id="debug-call2",
            history=[],
            context=notebook_context,
            mode="agentic",
            explicit_mode="agentic",
            context_meta=context_meta,
        )
    finally:
        for p in patches:
            p.stop()

    print(
        f"\nREACT_DEBUG fake LLM create() calls={len(fake.chat.completions.call_log)}",
        file=sys.stderr,
    )
    for entry in fake.chat.completions.call_log:
        print(f"  call #{entry['n'] + 1}: messages_len={entry['message_count']}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
