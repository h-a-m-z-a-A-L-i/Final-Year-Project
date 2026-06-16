import json
import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import tool_call_terminal as tct


def test_trace_respects_disable(monkeypatch, capsys):
    monkeypatch.setenv("TOOL_CALL_TERMINAL_TRACE", "0")
    assert tct.enabled() is False
    tct.log_tool_call("run_cell", {"cell_index": 1})
    tct.log_tool_result("run_cell", {"cell_index": 1}, {"ok": True})
    captured = capsys.readouterr()
    assert captured.err == ""


def test_trace_emits_when_enabled(monkeypatch, capsys):
    monkeypatch.setenv("TOOL_CALL_TERMINAL_TRACE", "1")
    tct.trace_tools_parsed(0, [{"function": {"name": "edit_cell_by_index"}}])
    captured = capsys.readouterr()
    assert "PARSE" in captured.err
    assert "edit_cell_by_index" in captured.err


def test_trace_writes_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_CALL_TERMINAL_TRACE", "1")
    log_path = tmp_path / "agentic_tool_trace.jsonl"
    monkeypatch.setattr(tct, "_trace_log_path", lambda: log_path)
    tct.trace_tool_exec("run_cell_by_index", {"cell_index": 2}, {"ok": True}, phase="batch")
    assert log_path.is_file()
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    events = [r["event"] for r in rows]
    assert "result" in events
    assert "exec" in events
    result_row = next(r for r in rows if r["event"] == "result")
    assert result_row["tool"] == "run_cell_by_index"
    assert result_row["ok"] is True
    assert result_row["phase"] == "batch"
    assert result_row["result"] == {"ok": True}


def test_log_tool_call_and_result_separate_events(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_CALL_TERMINAL_TRACE", "1")
    log_path = tmp_path / "agentic_tool_trace.jsonl"
    monkeypatch.setattr(tct, "_trace_log_path", lambda: log_path)
    tct.log_tool_call("insert_cell", {"direction": "below", "content": "x=1"}, round_idx=1, phase="write")
    tct.log_tool_result(
        "insert_cell",
        {"direction": "below", "content": "x=1"},
        {"ok": True},
        round_idx=1,
        phase="write",
    )
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["event"] == "dispatch"
    assert rows[0]["round"] == 1
    assert rows[0]["batch_id"] == "r1"
    assert rows[1]["event"] == "result"
    assert rows[1]["ok"] is True


def test_notebook_slug_from_url():
    url = "https://www.kaggle.com/code/alice/my-notebook/edit"
    assert tct.notebook_slug_from_url(url) == "my-notebook"
    assert tct.notebook_slug_from_url("") == ""


def test_trace_batch_end(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_CALL_TERMINAL_TRACE", "1")
    log_path = tmp_path / "agentic_tool_trace.jsonl"
    monkeypatch.setattr(tct, "_trace_log_path", lambda: log_path)
    tct.trace_batch_end(2, ok=True, detail="verified")
    row = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert row["event"] == "batch_end"
    assert row["round"] == 2
    assert row["batch_id"] == "r2"
    assert row["ok"] is True
