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
    tct.trace_tool_exec("run_cell", {"cell_index": 1}, {"ok": True})
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
    row = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert row["event"] == "exec"
    assert row["tool"] == "run_cell_by_index"
    assert row["ok"] is True
    assert row["phase"] == "batch"
