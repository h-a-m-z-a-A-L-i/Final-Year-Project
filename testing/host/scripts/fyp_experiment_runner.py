#!/usr/bin/env python3
"""
FYP experiment runner for Notebook Agent v1.

Executes predefined benchmark prompts and records structured metrics without
modifying production host/extension code. Results are written to:

  testing/host/data/logs/fyp_experiment_results.json
  testing/host/data/logs/fyp_experiment_summary.md

Modes
-----
harness (default)
    Invokes streaming._run_streaming_chat in-process with browser tools mocked.
    Use --live-llm to call the real Cerebras API (requires CEREBRAS_API_KEY).

collect
    Parse agentic_tool_trace.jsonl + token_usage.jsonl for markers written by
    a prior harness/live UI session (see --markers-file).

Examples
--------
  python testing/host/scripts/fyp_experiment_runner.py
  python testing/host/scripts/fyp_experiment_runner.py --live-llm
  python testing/host/scripts/fyp_experiment_runner.py --only agentic_edit_cell
  python testing/host/scripts/fyp_experiment_runner.py --mode collect
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

_HOST_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _HOST_DIR.parents[1]
_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEFAULT_BENCHMARKS = _SCRIPTS_DIR / "fyp_experiment_benchmarks.json"
_LOG_DIR = _HOST_DIR / "data" / "logs"
_RESULTS_PATH = _LOG_DIR / "fyp_experiment_results.json"
_SUMMARY_PATH = _LOG_DIR / "fyp_experiment_summary.md"
_MARKERS_PATH = _LOG_DIR / "fyp_experiment_markers.jsonl"
_TRACE_PATH = _LOG_DIR / "agentic_tool_trace.jsonl"
_TOKEN_PATH = _HOST_DIR / "data" / "meta" / "token_usage.jsonl"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Tool classification (mirrors agentic_batch_executor.py)
# ---------------------------------------------------------------------------

READ_TOOLS = frozenset({
    "notebook_get_cell",
    "notebook_get_cells",
    "notebook_find_symbol",
    "notebook_recommend_placement",
    "notebook_list_cells",
    "notebook_graph_query",
    "notebook_search",
    "notebook_overview",
    "notebook_executed_cells",
    "notebook_snapshot_status",
    "notebook_cell_neighbors",
    "list_cells",
    "get_cell",
    "notebook_get_cell_by_index",
})

WRITE_TOOLS = frozenset({
    "click_cell",
    "select_cell_by_index",
    "insert_cell",
    "edit_cell_by_index",
    "delete_by_index",
    "creating_markdown_by_index",
    "edit_cell",
    "run_cell",
    "run_cell_by_index",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _read_jsonl_from_offset(path: Path, offset: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        fh.seek(max(0, offset))
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _file_offset(path: Path) -> int:
    if not path.is_file():
        return 0
    return path.stat().st_size


@dataclass
class ExperimentMetrics:
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    notebook_reads: int = 0
    notebook_writes: int = 0
    repair_rounds: int = 0
    runtime_errors: int = 0
    completion_status: str = "unknown"
    execution_time: float = 0.0
    token_usage: dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
        }
    )
    planner_usage: int = 0
    semantic_index_hits: int = 0
    dependency_graph_hits: int = 0
    runtime_state_hits: int = 0


@dataclass
class LogOffsets:
    trace: int = 0
    token: int = 0


def _append_marker(payload: dict[str, Any]) -> None:
    _MARKERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = dict(payload)
    row.setdefault("ts", _utc_now())
    with _MARKERS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _snapshot_offsets() -> LogOffsets:
    return LogOffsets(trace=_file_offset(_TRACE_PATH), token=_file_offset(_TOKEN_PATH))


def _tool_name(raw: str) -> str:
    return str(raw or "").strip().lower()


def _bump_tool_metrics(metrics: ExperimentMetrics, tool: str) -> None:
    if not tool:
        return
    metrics.total_tool_calls += 1
    if tool in READ_TOOLS:
        metrics.notebook_reads += 1
    elif tool in WRITE_TOOLS and tool not in ("run_cell", "run_cell_by_index"):
        metrics.notebook_writes += 1


def _merge_token_usage(into: dict[str, int], addition: dict[str, Any] | None) -> None:
    if not isinstance(addition, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens"):
        into[key] = int(into.get(key, 0) or 0) + int(addition.get(key, 0) or 0)
    into["requests"] = int(into.get("requests", 0) or 0) + int(addition.get("requests", 0) or 0)


def _subsystem_hits(notebook_key: str) -> dict[str, int]:
    """Return 0/1 hit flags from host meta stores (read-only)."""
    key = str(notebook_key or "").strip()
    hits = {
        "planner_usage": 0,
        "semantic_index_hits": 0,
        "dependency_graph_hits": 0,
        "runtime_state_hits": 0,
    }
    if not key:
        return hits
    try:
        from testing.host.agent_planner import load_agent_plan, planner_enabled

        if planner_enabled():
            plan = load_agent_plan(key)
            if isinstance(plan, dict) and plan.get("plan"):
                hits["planner_usage"] = 1
    except Exception:
        pass
    try:
        from testing.host.notebook_semantic_index import load_semantic_index, semantic_index_enabled

        if semantic_index_enabled() and load_semantic_index(key):
            hits["semantic_index_hits"] = 1
    except Exception:
        pass
    try:
        from testing.host.notebook_dependency_graph import (
            dependency_graph_enabled,
            load_dependency_graph,
        )

        graph = load_dependency_graph(key) if dependency_graph_enabled() else None
        if isinstance(graph, dict) and graph.get("edges"):
            hits["dependency_graph_hits"] = 1
    except Exception:
        pass
    try:
        from testing.host.runtime_state import load_runtime_state, runtime_state_enabled

        runtime = load_runtime_state(key) if runtime_state_enabled() else None
        if isinstance(runtime, dict) and (
            runtime.get("metrics") or runtime.get("dataframes") or runtime.get("models")
        ):
            hits["runtime_state_hits"] = 1
    except Exception:
        pass
    return hits


def collect_metrics_from_logs(
    *,
    trace_rows: list[dict[str, Any]],
    token_rows: list[dict[str, Any]],
    session_id: str,
    notebook_key: str,
    stream_messages: list[dict[str, Any]] | None = None,
    llm_call_count: int | None = None,
    execution_time: float = 0.0,
    error_text: str = "",
) -> ExperimentMetrics:
    """Aggregate metrics from trace/token logs and optional captured stream messages."""
    metrics = ExperimentMetrics(execution_time=round(float(execution_time), 4))

    session_id = str(session_id or "").strip()
    for row in trace_rows:
        row_session = str(row.get("session_id") or "")
        if session_id and row_session and row_session != session_id:
            continue

        event = str(row.get("event") or "").lower()
        tool = _tool_name(row.get("tool"))

        if event == "parse":
            if row.get("recovery"):
                metrics.repair_rounds += 1

        if event == "verify":
            executed = row.get("executed") or []
            if isinstance(executed, list):
                for item in executed:
                    if isinstance(item, dict):
                        _bump_tool_metrics(metrics, _tool_name(item.get("tool")))
            exec_err = row.get("execution_error")
            if isinstance(exec_err, dict) and exec_err:
                metrics.runtime_errors += 1
            if row.get("verified") is False or row.get("goal_verified") is False:
                metrics.repair_rounds += 1

        if event in ("dispatch", "exec"):
            _bump_tool_metrics(metrics, tool)

        if event in ("llm_error", "run_error"):
            metrics.runtime_errors += 1

        if event == "result" and row.get("ok") is False:
            if tool in ("run_cell", "run_cell_by_index") or row.get("error"):
                metrics.runtime_errors += 1

    # Token usage for session
    for row in token_rows:
        if session_id and str(row.get("session_id") or "") != session_id:
            continue
        usage = row.get("usage")
        if isinstance(usage, dict):
            _merge_token_usage(metrics.token_usage, usage)
        if session_id:
            metrics.token_usage["requests"] = max(
                int(metrics.token_usage.get("requests", 0)),
                sum(1 for r in token_rows if str(r.get("session_id") or "") == session_id),
            )

    # LLM calls: prefer explicit count, else react_round / token requests
    react_rounds = sum(1 for r in trace_rows if r.get("event") == "react_round")
    parse_rounds = len({r.get("round") for r in trace_rows if r.get("event") == "parse"})
    if llm_call_count is not None:
        metrics.total_llm_calls = int(llm_call_count)
    elif metrics.token_usage.get("requests"):
        metrics.total_llm_calls = int(metrics.token_usage["requests"])
    else:
        metrics.total_llm_calls = max(react_rounds, parse_rounds)

    # Stream end messages
    stopped = False
    stream_error = error_text
    for msg in stream_messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("type") != "CHAT_STREAM_END":
            continue
        if msg.get("stopped"):
            stopped = True
        if msg.get("error"):
            stream_error = str(msg.get("error"))
        tu = msg.get("tokenUsage") or msg.get("token_usage")
        if isinstance(tu, dict):
            _merge_token_usage(
                metrics.token_usage,
                {
                    "prompt_tokens": tu.get("promptTokens") or tu.get("prompt_tokens"),
                    "completion_tokens": tu.get("completionTokens") or tu.get("completion_tokens"),
                    "cached_tokens": tu.get("cachedTokens") or tu.get("cached_tokens"),
                    "total_tokens": tu.get("totalTokens") or tu.get("total_tokens"),
                    "requests": tu.get("requests") or 1,
                },
            )

    if stream_error:
        metrics.completion_status = "error"
    elif stopped:
        metrics.completion_status = "stopped"
    elif metrics.runtime_errors > 0:
        metrics.completion_status = "failed"
    elif metrics.repair_rounds > 0 and metrics.total_tool_calls == 0:
        metrics.completion_status = "partial"
    elif metrics.total_tool_calls > 0 or metrics.total_llm_calls > 0:
        metrics.completion_status = "success"
    else:
        metrics.completion_status = "partial"

    hits = _subsystem_hits(notebook_key)
    metrics.planner_usage = hits["planner_usage"]
    metrics.semantic_index_hits = hits["semantic_index_hits"]
    metrics.dependency_graph_hits = hits["dependency_graph_hits"]
    metrics.runtime_state_hits = hits["runtime_state_hits"]

    return metrics


# ---------------------------------------------------------------------------
# Harness LLM mocks
# ---------------------------------------------------------------------------


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


class _FakeDelta:
    def __init__(self, content: str = ""):
        self.content = content


class _FakeStreamChoice:
    def __init__(self, content: str = ""):
        self.delta = _FakeDelta(content)


class _FakeStreamEvent:
    def __init__(self, content: str = ""):
        self.choices = [_FakeStreamChoice(content)]


class _FakeCompletions:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    def create(self, **kwargs):
        self.calls.append(
            {
                "messages_len": len(kwargs.get("messages") or []),
                "stream": bool(kwargs.get("stream")),
            }
        )
        idx = min(self._n, max(len(self._responses) - 1, 0))
        self._n += 1
        resp = self._responses[idx]
        if kwargs.get("stream"):
            content = ""
            if resp.choices:
                content = str(resp.choices[0].message.content or "")
            return iter([_FakeStreamEvent(content)])
        return resp


class _FakeLLMClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(responses)


def _native_tool_call(name: str, args: dict, call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _format_mock_args(args: dict, *, url: str, tab_id: int) -> dict:
    out = {}
    for key, val in (args or {}).items():
        if isinstance(val, str):
            out[key] = val.format(url=url, tab_id=tab_id)
        else:
            out[key] = val
    out.setdefault("url", url)
    out.setdefault("tab_id", tab_id)
    return out


def _build_mock_responses(case: dict[str, Any], *, url: str, tab_id: int) -> list[_FakeResponse]:
    mock = case.get("harness_mock") or {}
    rounds = mock.get("rounds") or []
    if not rounds:
        rounds = [
            {"tool_calls": [{"name": "notebook_list_cells", "args": {"url": "{url}"}}]},
            {"content": "Benchmark complete."},
        ]
    responses: list[_FakeResponse] = []
    for i, rnd in enumerate(rounds):
        tool_calls_raw = rnd.get("tool_calls") or []
        if tool_calls_raw:
            tcs = []
            for j, tc in enumerate(tool_calls_raw):
                name = str(tc.get("name") or "")
                args = _format_mock_args(tc.get("args") or {}, url=url, tab_id=tab_id)
                tcs.append(_native_tool_call(name, args, f"mock-{i}-{j}"))
            responses.append(_FakeResponse(tool_calls=tcs))
        else:
            responses.append(_FakeResponse(content=str(rnd.get("content") or "Done.")))
    return responses


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


def _write_batch_verification(tool_calls: list | None = None):
    executed = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = str(fn.get("name") or "")
        executed.append({"tool": name, "dispatched": True})
    return {
        "verified": True,
        "batch_executed": True,
        "fire_and_forget": True,
        "tool_queue_status": "dispatched",
        "tool_queue_complete": True,
        "run_queue_complete": True,
        "executed": executed or [{"tool": "edit_cell_by_index", "dispatched": True}],
    }


@contextmanager
def _harness_patches(
    *,
    fake_client: Any | None,
    emitted: list[str],
    live_llm: bool,
) -> Iterator[None]:
    def _capture_delta(*_a, delta: str = "", **_k):
        if delta:
            emitted.append(delta)

    def _batch_side_effect(tool_calls, **kwargs):
        if kwargs.get("force_implementation"):
            return _write_batch_verification(tool_calls)
        names = []
        for tc in tool_calls or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                names.append(str(fn.get("name") or ""))
        if any(n in READ_TOOLS for n in names):
            return _list_cells_verification()
        return _write_batch_verification(tool_calls)

    patches = [
        patch.dict(
            os.environ,
            {
                "LLM_AGENTIC_ENABLED": "1",
                "TOOL_CALL_TERMINAL_TRACE": "1",
                "AGENTIC_MANDATORY_TWO_PHASE": "1",
                "AGENTIC_MAX_TOOL_ROUNDS": "2",
                "AGENTIC_FIRE_AND_FORGET": "1",
            },
            clear=False,
        ),
        patch("testing.host.streaming.send_msg", lambda *a, **k: None),
        patch("testing.host.streaming.memory_store.append", lambda *a, **k: None),
        patch("testing.host.streaming._wait_for_request_slot", lambda *a, **k: True),
        patch("testing.host.streaming._check_token_limits", lambda: (True, {})),
        patch("testing.host.streaming._record_llm_usage", lambda *a, **k: None),
        patch("testing.host.streaming._finalize_request_attempt", lambda *a, **k: None),
        patch("testing.host.streaming._record_request_attempt", lambda *a, **k: None),
        patch("testing.host.streaming._begin_llm_request", lambda *a, **k: str(uuid.uuid4())),
        patch("testing.host.agentic_tool_chain.build_direct_edit_from_prompt", lambda *a, **k: None),
        patch("testing.host.notebook_query.prefetch_notebook_queries", lambda **k: ("", [])),
        patch("testing.host.streaming.AGENTIC_FIRE_AND_FORGET", True),
        patch("testing.host.streaming.AGENTIC_MAX_TOOL_ROUNDS", 2),
        patch("testing.host.streaming.AGENTIC_MAX_QUERY_ROUNDS", 1),
        patch("testing.host.streaming.AGENTIC_MANDATORY_TWO_PHASE", True),
        patch("testing.host.streaming._emit_stream_delta", _capture_delta),
        patch(
            "testing.host.agentic_batch_executor.execute_agentic_batch",
            side_effect=_batch_side_effect,
        ),
    ]
    for p in patches:
        p.start()
    try:
        import testing.host.streaming as streaming

        if fake_client is not None and not live_llm:
            streaming._LLM_CLIENT = fake_client
        yield
    finally:
        for p in patches:
            p.stop()


def _pack_context_for_case(case: dict[str, Any], defaults: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    url = str(case.get("url") or defaults.get("url") or "").strip()
    mode = str(case.get("mode") or defaults.get("mode") or "agentic").strip().lower()
    prompt = str(case.get("prompt") or "")
    notebook_key = str(case.get("notebook_key") or defaults.get("notebook_key") or url)
    try:
        from testing.host.notebook_context import pack_context

        pack = pack_context(mode=mode, url=url, prompt=prompt, dep_manager=None, bot_state={})
        return pack.text, {
            "coverage": pack.coverage,
            "snapshot": pack.snapshot,
            "history_key": notebook_key,
            "snapshot_url": url,
            "active_key": str(defaults.get("tab_id", 424242)),
        }
    except Exception:
        return "Benchmark notebook context.", {
            "history_key": notebook_key,
            "snapshot_url": url,
            "active_key": str(defaults.get("tab_id", 424242)),
        }


def run_harness_case(
    case: dict[str, Any],
    defaults: dict[str, Any],
    *,
    experiment_id: str,
    live_llm: bool,
) -> dict[str, Any]:
    prompt_id = str(case.get("id") or "unknown")
    session_id = f"fyp-{experiment_id}-{prompt_id}-{uuid.uuid4().hex[:8]}"
    url = str(case.get("url") or defaults.get("url") or "").strip()
    tab_id = int(case.get("tab_id") or defaults.get("tab_id") or 424242)
    mode = str(case.get("mode") or defaults.get("mode") or "agentic").strip().lower()
    notebook_key = str(case.get("notebook_key") or defaults.get("notebook_key") or url)
    prompt = str(case.get("prompt") or "")

    offsets = _snapshot_offsets()
    _append_marker(
        {
            "event": "start",
            "experiment_id": experiment_id,
            "prompt_id": prompt_id,
            "session_id": session_id,
            "mode": mode,
            "url": url,
        }
    )

    context, context_meta = _pack_context_for_case(case, defaults)
    context_meta["cache_session_id"] = session_id

    captured_messages: list[dict[str, Any]] = []
    emitted: list[str] = []
    error_text = ""
    llm_calls: int | None = None

    fake_client = None if live_llm else _FakeLLMClient(_build_mock_responses(case, url=url, tab_id=tab_id))

    def _capture_send(msg: dict):
        if isinstance(msg, dict):
            captured_messages.append(msg)

    t0 = time.perf_counter()
    try:
        import testing.host.streaming as streaming
        from testing.host.agentic_mode import set_dashboard_agentic_enabled

        set_dashboard_agentic_enabled(True)
        with _harness_patches(fake_client=fake_client, emitted=emitted, live_llm=live_llm):
            with patch("testing.host.streaming.send_msg", _capture_send):
                streaming._run_streaming_chat(
                    notebook_key,
                    prompt,
                    tab_id=tab_id,
                    session_id=session_id,
                    history=[],
                    context=context,
                    mode=mode,
                    explicit_mode=mode,
                    context_meta=context_meta,
                )
        if fake_client is not None:
            llm_calls = len(fake_client.chat.completions.calls)
    except Exception as exc:
        error_text = str(exc)
    elapsed = time.perf_counter() - t0

    trace_rows = _read_jsonl_from_offset(_TRACE_PATH, offsets.trace)
    token_rows = _read_jsonl_from_offset(_TOKEN_PATH, offsets.token)
    metrics = collect_metrics_from_logs(
        trace_rows=trace_rows,
        token_rows=token_rows,
        session_id=session_id,
        notebook_key=notebook_key,
        stream_messages=captured_messages,
        llm_call_count=llm_calls,
        execution_time=elapsed,
        error_text=error_text,
    )
    if not error_text:
        for msg in captured_messages:
            if isinstance(msg, dict) and msg.get("type") == "CHAT_STREAM_END" and msg.get("error"):
                error_text = str(msg.get("error"))
                break

    response_text = ""
    for msg in captured_messages:
        if isinstance(msg, dict) and msg.get("type") == "CHAT_STREAM_END":
            response_text = str(msg.get("response") or response_text)
    if not response_text.strip():
        response_text = "".join(emitted)

    result = {
        "id": prompt_id,
        "name": case.get("name") or prompt_id,
        "kernel_id": case.get("kernel_id"),
        "snapshot_file": case.get("snapshot_file"),
        "session_id": session_id,
        "prompt": prompt,
        "mode": mode,
        "url": url,
        "notebook_key": notebook_key,
        "live_llm": live_llm,
        "error": error_text or None,
        "response_text": response_text[:16000] if response_text else "",
        "metrics": asdict(metrics),
    }

    _append_marker(
        {
            "event": "end",
            "experiment_id": experiment_id,
            "prompt_id": prompt_id,
            "session_id": session_id,
            "completion_status": metrics.completion_status,
            "execution_time": metrics.execution_time,
        }
    )
    return result


def collect_from_markers(
    *,
    experiment_id: str | None = None,
    markers_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Build run results from prior start/end markers (UI or harness)."""
    markers = _read_jsonl(markers_path or _MARKERS_PATH)
    starts = [m for m in markers if m.get("event") == "start"]
    if experiment_id:
        starts = [m for m in starts if m.get("experiment_id") == experiment_id]

    runs: list[dict[str, Any]] = []
    for start in starts:
        session_id = str(start.get("session_id") or "")
        prompt_id = str(start.get("prompt_id") or "unknown")
        end = next(
            (
                m
                for m in markers
                if m.get("event") == "end" and m.get("session_id") == session_id
            ),
            {},
        )
        trace_rows = _read_jsonl(_TRACE_PATH)
        token_rows = _read_jsonl(_TOKEN_PATH)
        notebook_key = str(start.get("notebook_key") or start.get("url") or "")
        elapsed = float(end.get("execution_time") or 0.0)
        metrics = collect_metrics_from_logs(
            trace_rows=trace_rows,
            token_rows=token_rows,
            session_id=session_id,
            notebook_key=notebook_key,
            execution_time=elapsed,
        )
        runs.append(
            {
                "id": prompt_id,
                "name": start.get("name") or prompt_id,
                "session_id": session_id,
                "prompt": start.get("prompt"),
                "mode": start.get("mode"),
                "url": start.get("url"),
                "notebook_key": notebook_key,
                "live_llm": None,
                "error": None,
                "metrics": asdict(metrics),
                "collected_from_markers": True,
            }
        )
    return runs


def _aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {}
    n = len(runs)
    totals = ExperimentMetrics()
    status_counts: dict[str, int] = {}

    for run in runs:
        m = run.get("metrics") or {}
        totals.total_llm_calls += int(m.get("total_llm_calls") or 0)
        totals.total_tool_calls += int(m.get("total_tool_calls") or 0)
        totals.notebook_reads += int(m.get("notebook_reads") or 0)
        totals.notebook_writes += int(m.get("notebook_writes") or 0)
        totals.repair_rounds += int(m.get("repair_rounds") or 0)
        totals.runtime_errors += int(m.get("runtime_errors") or 0)
        totals.execution_time += float(m.get("execution_time") or 0.0)
        totals.planner_usage += int(m.get("planner_usage") or 0)
        totals.semantic_index_hits += int(m.get("semantic_index_hits") or 0)
        totals.dependency_graph_hits += int(m.get("dependency_graph_hits") or 0)
        totals.runtime_state_hits += int(m.get("runtime_state_hits") or 0)
        _merge_token_usage(totals.token_usage, m.get("token_usage"))
        st = str(m.get("completion_status") or "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "run_count": n,
        "completion_status_counts": status_counts,
        "avg_execution_time": round(totals.execution_time / n, 4),
        "total_llm_calls": totals.total_llm_calls,
        "total_tool_calls": totals.total_tool_calls,
        "total_notebook_reads": totals.notebook_reads,
        "total_notebook_writes": totals.notebook_writes,
        "total_repair_rounds": totals.repair_rounds,
        "total_runtime_errors": totals.runtime_errors,
        "token_usage": totals.token_usage,
        "planner_usage_runs": totals.planner_usage,
        "semantic_index_hit_runs": totals.semantic_index_hits,
        "dependency_graph_hit_runs": totals.dependency_graph_hits,
        "runtime_state_hit_runs": totals.runtime_state_hits,
    }


def generate_summary_md(payload: dict[str, Any]) -> str:
    agg = payload.get("aggregate") or {}
    lines = [
        "# Notebook Agent v1 — FYP Experiment Summary",
        "",
        f"- **Experiment ID:** `{payload.get('experiment_id', '')}`",
        f"- **Agent:** {payload.get('agent', 'Notebook Agent v1')}",
        f"- **Mode:** {payload.get('mode', '')}",
        f"- **Live LLM:** {payload.get('live_llm', False)}",
        f"- **Started:** {payload.get('started_at', '')}",
        f"- **Finished:** {payload.get('finished_at', '')}",
        f"- **Benchmarks file:** `{payload.get('benchmarks_file', '')}`",
        "",
        "## Aggregate",
        "",
        f"| Metric | Value |",
        f"|--------|------:|",
        f"| Runs | {agg.get('run_count', 0)} |",
        f"| Avg execution time (s) | {agg.get('avg_execution_time', 0)} |",
        f"| Total LLM calls | {agg.get('total_llm_calls', 0)} |",
        f"| Total tool calls | {agg.get('total_tool_calls', 0)} |",
        f"| Notebook reads | {agg.get('total_notebook_reads', 0)} |",
        f"| Notebook writes | {agg.get('total_notebook_writes', 0)} |",
        f"| Repair rounds | {agg.get('total_repair_rounds', 0)} |",
        f"| Runtime errors | {agg.get('total_runtime_errors', 0)} |",
    ]
    tu = agg.get("token_usage") or {}
    lines.extend(
        [
            f"| Prompt tokens | {tu.get('prompt_tokens', 0)} |",
            f"| Completion tokens | {tu.get('completion_tokens', 0)} |",
            f"| Total tokens | {tu.get('total_tokens', 0)} |",
            f"| Planner usage (runs) | {agg.get('planner_usage_runs', 0)} |",
            f"| Semantic index hits (runs) | {agg.get('semantic_index_hit_runs', 0)} |",
            f"| Dependency graph hits (runs) | {agg.get('dependency_graph_hit_runs', 0)} |",
            f"| Runtime state hits (runs) | {agg.get('runtime_state_hit_runs', 0)} |",
            "",
            "### Completion status",
            "",
        ]
    )
    for status, count in sorted((agg.get("completion_status_counts") or {}).items()):
        lines.append(f"- **{status}:** {count}")
    lines.extend(["", "## Per-prompt results", ""])
    for run in payload.get("runs") or []:
        m = run.get("metrics") or {}
        lines.append(f"### {run.get('name') or run.get('id')} (`{run.get('id')}`)")
        lines.append("")
        lines.append(f"- Mode: `{run.get('mode')}`")
        lines.append(f"- Status: **{m.get('completion_status', 'unknown')}**")
        lines.append(f"- Execution time: {m.get('execution_time', 0)} s")
        lines.append(f"- LLM calls: {m.get('total_llm_calls', 0)}")
        lines.append(f"- Tool calls: {m.get('total_tool_calls', 0)}")
        lines.append(f"- Reads / writes: {m.get('notebook_reads', 0)} / {m.get('notebook_writes', 0)}")
        lines.append(f"- Repair rounds: {m.get('repair_rounds', 0)}")
        lines.append(f"- Runtime errors: {m.get('runtime_errors', 0)}")
        tu_run = m.get("token_usage") or {}
        lines.append(
            f"- Tokens: {tu_run.get('total_tokens', 0)} "
            f"(prompt {tu_run.get('prompt_tokens', 0)}, "
            f"completion {tu_run.get('completion_tokens', 0)})"
        )
        lines.append(
            f"- Subsystems: planner={m.get('planner_usage', 0)}, "
            f"semantic={m.get('semantic_index_hits', 0)}, "
            f"deps={m.get('dependency_graph_hits', 0)}, "
            f"runtime={m.get('runtime_state_hits', 0)}"
        )
        if run.get("error"):
            lines.append(f"- Error: `{run['error']}`")
        lines.append("")
    lines.append(f"_Generated {_utc_now()}_")
    lines.append("")
    return "\n".join(lines)


def load_benchmarks(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Benchmarks file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("prompts"), list):
        raise ValueError("Benchmarks file must contain a 'prompts' list")
    return data


def run_experiment(
    *,
    benchmarks_path: Path,
    mode: str,
    live_llm: bool,
    only: set[str] | None,
    experiment_id: str | None,
) -> dict[str, Any]:
    bench = load_benchmarks(benchmarks_path)
    experiment_id = experiment_id or f"exp-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    defaults = bench.get("defaults") or {}
    prompts = bench.get("prompts") or []

    if only:
        prompts = [p for p in prompts if str(p.get("id") or "") in only]
        if not prompts:
            raise SystemExit(f"No benchmarks matched --only {sorted(only)}")

    started_at = _utc_now()
    runs: list[dict[str, Any]] = []

    if mode == "collect":
        runs = collect_from_markers(experiment_id=experiment_id)
    else:
        for case in prompts:
            runs.append(
                run_harness_case(
                    case,
                    defaults,
                    experiment_id=experiment_id,
                    live_llm=live_llm,
                )
            )

    finished_at = _utc_now()
    payload = {
        "agent": bench.get("agent") or "Notebook Agent v1",
        "experiment_id": experiment_id,
        "mode": mode,
        "live_llm": live_llm if mode != "collect" else None,
        "started_at": started_at,
        "finished_at": finished_at,
        "benchmarks_file": str(benchmarks_path),
        "runs": runs,
        "aggregate": _aggregate_runs(runs),
    }
    return payload


def generate_ml_pipeline_report_md(payload: dict[str, Any], run: dict[str, Any]) -> str:
    """Focused markdown report for BENCHMARK_ML_PIPELINE runs."""
    m = run.get("metrics") or {}
    agg = payload.get("aggregate") or {}
    status = str(m.get("completion_status") or "unknown")
    success = 1 if status == "success" else 0
    run_count = int(agg.get("run_count") or 1)
    completion_rate = round(100.0 * success / max(run_count, 1), 1)
    kernel_id = run.get("kernel_id") or ""
    snapshot = run.get("snapshot_file") or ""
    prompt = str(run.get("prompt") or "").strip()

    lines = [
        "# BENCHMARK_ML_PIPELINE — Report",
        "",
        "## Overview",
        "",
        f"- **Benchmark:** BENCHMARK_ML_PIPELINE",
        f"- **Agent:** {payload.get('agent', 'Notebook Agent v1')}",
        f"- **Kernel ID:** `{kernel_id}`",
        f"- **Notebook key:** `{run.get('notebook_key', '')}`",
        f"- **Snapshot:** `persistent/{snapshot}`",
        f"- **Mode:** {payload.get('mode', 'harness')} | **Live LLM:** {payload.get('live_llm', False)}",
        f"- **Started:** {payload.get('started_at', '')}",
        f"- **Finished:** {payload.get('finished_at', '')}",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Completion rate | {completion_rate}% ({success}/{run_count}) |",
        f"| LLM calls | {m.get('total_llm_calls', 0)} |",
        f"| Tool calls | {m.get('total_tool_calls', 0)} |",
        f"| Repair rounds | {m.get('repair_rounds', 0)} |",
        f"| Runtime errors | {m.get('runtime_errors', 0)} |",
        f"| Execution time (s) | {m.get('execution_time', 0)} |",
        "",
        "## Prompt",
        "",
        "```",
        prompt,
        "```",
        "",
        "## Run details",
        "",
        f"- **Status:** {status}",
        f"- **Session:** `{run.get('session_id', '')}`",
        f"- **URL:** {run.get('url', '')}",
        f"- **LLM calls:** {m.get('total_llm_calls', 0)}",
        f"- **Tool calls:** {m.get('total_tool_calls', 0)}",
        f"- **Notebook reads / writes:** {m.get('notebook_reads', 0)} / {m.get('notebook_writes', 0)}",
        f"- **Repair rounds:** {m.get('repair_rounds', 0)}",
        f"- **Runtime errors:** {m.get('runtime_errors', 0)}",
        f"- **Execution time:** {m.get('execution_time', 0)} s",
    ]
    if run.get("error"):
        lines.append(f"- **Error:** `{run['error']}`")
    lines.extend(["", f"_Generated {_utc_now()}_", ""])
    return "\n".join(lines)


def generate_ml_pipeline_summary_md(payload: dict[str, Any], run: dict[str, Any]) -> str:
    """Concise per-kernel summary for BENCHMARK_ML_PIPELINE."""
    m = run.get("metrics") or {}
    agg = payload.get("aggregate") or {}
    status = str(m.get("completion_status") or "unknown")
    success = 1 if status == "success" else 0
    run_count = int(agg.get("run_count") or 1)
    completion_rate = round(100.0 * success / max(run_count, 1), 1)
    kernel_id = run.get("kernel_id") or ""
    snapshot = run.get("snapshot_file") or ""

    lines = [
        f"# BENCHMARK_ML_PIPELINE — Summary (kernel {kernel_id})",
        "",
        "## Notebook",
        "",
        f"- **Name:** {run.get('name', '')}",
        f"- **Kernel ID:** `{kernel_id}`",
        f"- **Notebook key:** `{run.get('notebook_key', '')}`",
        f"- **URL:** {run.get('url', '')}",
        f"- **Snapshot:** `persistent/{snapshot}`",
        f"- **Experiment ID:** `{payload.get('experiment_id', '')}`",
        f"- **Live LLM:** {payload.get('live_llm', False)}",
        f"- **Finished:** {payload.get('finished_at', '')}",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Completion rate | {completion_rate}% |",
        f"| LLM calls | {m.get('total_llm_calls', 0)} |",
        f"| Tool calls | {m.get('total_tool_calls', 0)} |",
        f"| Notebook reads | {m.get('notebook_reads', 0)} |",
        f"| Notebook writes | {m.get('notebook_writes', 0)} |",
        f"| Repair rounds | {m.get('repair_rounds', 0)} |",
        f"| Runtime errors | {m.get('runtime_errors', 0)} |",
        f"| Execution time (s) | {m.get('execution_time', 0)} |",
        "",
        "## Status",
        "",
        f"**{status}** — {success}/{run_count} run(s) completed successfully.",
        "",
        f"_Generated {_utc_now()}_",
        "",
    ]
    return "\n".join(lines)


def write_ml_pipeline_outputs(
    payload: dict[str, Any],
    run: dict[str, Any],
    *,
    kernel_id: int | str,
) -> dict[str, str]:
    """Write per-kernel results, summary, and report files."""
    kid = str(kernel_id)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    results_path = _LOG_DIR / f"BENCHMARK_ML_PIPELINE_kaggle_kernel_{kid}_results.json"
    summary_path = _LOG_DIR / f"BENCHMARK_ML_PIPELINE_kaggle_kernel_{kid}_summary.md"
    report_path = _LOG_DIR / f"BENCHMARK_ML_PIPELINE_kaggle_kernel_{kid}_report.md"

    results_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary_path.write_text(generate_ml_pipeline_summary_md(payload, run), encoding="utf-8")
    report_path.write_text(generate_ml_pipeline_report_md(payload, run), encoding="utf-8")

    return {
        "results_path": str(results_path),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
    }


def regenerate_ml_pipeline_docs_from_results(results_path: Path) -> dict[str, str]:
    """Rebuild summary + report from an existing results JSON file."""
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    run = (payload.get("runs") or [{}])[0]
    kernel_id = run.get("kernel_id") or results_path.stem.split("_")[-1]
    return write_ml_pipeline_outputs(payload, run, kernel_id=kernel_id)


def write_outputs(
    payload: dict[str, Any],
    *,
    results_path: Path | None = None,
    summary_path: Path | None = None,
) -> tuple[Path, Path]:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_results = results_path or _RESULTS_PATH
    out_summary = summary_path or _SUMMARY_PATH
    out_results.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_summary.write_text(generate_summary_md(payload), encoding="utf-8")
    return out_results, out_summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FYP experiment runner for Notebook Agent v1")
    parser.add_argument(
        "--benchmarks",
        type=Path,
        default=_DEFAULT_BENCHMARKS,
        help="Path to benchmarks JSON",
    )
    parser.add_argument(
        "--mode",
        choices=("harness", "collect"),
        default="harness",
        help="harness: run in-process; collect: aggregate from markers",
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Use real Cerebras API (requires CEREBRAS_API_KEY); browser tools remain mocked",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="ID",
        help="Run only benchmark prompt ids",
    )
    parser.add_argument(
        "--experiment-id",
        default="",
        help="Optional experiment id (auto-generated if omitted)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List benchmark prompts and exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        bench = load_benchmarks(args.benchmarks)
        for case in bench.get("prompts") or []:
            print(f"{case.get('id')}\t{case.get('mode')}\t{case.get('name')}")
        return 0

    only = set(args.only) if args.only else None
    payload = run_experiment(
        benchmarks_path=args.benchmarks,
        mode=args.mode,
        live_llm=bool(args.live_llm),
        only=only,
        experiment_id=args.experiment_id or None,
    )
    results_path, summary_path = write_outputs(payload)
    print(f"Wrote {results_path}")
    print(f"Wrote {summary_path}")
    print(f"Runs: {len(payload.get('runs') or [])}")
    print(f"Aggregate status: {payload.get('aggregate', {}).get('completion_status_counts', {})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
