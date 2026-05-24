"""Prefetch tool evidence must not use role=tool without tool_call_id."""

import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.streaming import inject_prefetched_tool_context


def test_inject_prefetched_tool_context_uses_system_not_tool_role():
    messages = [{"role": "system", "content": "base"}, {"role": "user", "content": "hi"}]
    inject_prefetched_tool_context(
        messages,
        graph={"ok": True, "cells": []},
        cell_slice="cell 0: print(1)",
    )
    assert all(m.get("role") != "tool" for m in messages)
    assert len(messages) == 2
    system = messages[0]["content"]
    assert "Prefetched tool results" in system
    assert "notebook_graph_query" in system
    assert "cell_slice" in system
    assert json.dumps({"ok": True, "cells": []}) in system


def test_inject_prefetched_tool_context_inserts_system_when_missing():
    messages = [{"role": "user", "content": "hi"}]
    inject_prefetched_tool_context(messages, graph={"nodes": 1})
    assert messages[0]["role"] == "system"
    assert "notebook_graph_query" in messages[0]["content"]
