"""Tests for run_cell_verification fast EXEC DETECTED path."""

from __future__ import annotations

import json
from pathlib import Path

from testing.host.run_cell_verification import wait_for_run_verification


def test_normalize_execution_title():
    from testing.host.cell_execution_observer import normalize_execution_title, parse_execution_time_sec

    assert normalize_execution_title("Cell executed in 0.005s at 9:18am") == "Cell executed in 0.005s"
    assert parse_execution_time_sec("Cell executed in 0.005s at 9:18am") == 0.005


def test_read_host_log_prompt_signals(tmp_path, monkeypatch):
    from testing.host import cell_structure_live as csl

    log_path = tmp_path / "host.log"
    log_path.write_text(
        "[04:00:00] PROMPT_SIGNAL cell=2 text=Cell executed in 0.005s at 9:18am\n"
        "[04:00:01] PROMPT_SIGNAL cell=2 order=5 text=Cell execution is queued ts=123\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(csl, "_HOST_LOG_PATH", log_path)

    pairs, new_off = csl.read_host_log_prompt_signals(0, cell_index=2)
    assert len(pairs) == 2
    assert pairs[0][1].startswith("Cell executed in 0.005s")


def test_parse_execution_time_sec():
    from testing.host.cell_execution_observer import parse_execution_time_sec

    assert parse_execution_time_sec("Cell executed in 0.006s") == 0.006
    assert parse_execution_time_sec("Cell executed in 1.2s") == 1.2
    assert parse_execution_time_sec("Cell executed in 0.1 sec") == 0.1
    assert parse_execution_time_sec("") is None


def test_wait_for_run_verification_exec_detected_log(tmp_path, monkeypatch):
    log_path = tmp_path / "host.log"
    log_path.write_text("[12:00:00] warm\n", encoding="utf-8")
    offset = log_path.stat().st_size

    before = {
        "cells": [
            {
                "index": 1,
                "type": "code",
                "input": "x = 1",
                "output": "",
                "execution_order": 5,
            }
        ]
    }
    after = {
        "cells": [
            {
                "index": 1,
                "type": "code",
                "input": "x = 1",
                "output": "1\n",
                "execution_order": 6,
            }
        ]
    }

    monkeypatch.setattr("testing.host.config.LOG_PATH", log_path)
    monkeypatch.setattr(
        "testing.host.cell_structure_live._HOST_LOG_PATH",
        log_path,
    )

    log_path.write_text(
        log_path.read_text(encoding="utf-8") + "[12:00:01] EXEC DETECTED cell=1 order=6\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "testing.host.notebook_context.load_notebook_snapshot",
        lambda url: (after, "live"),
    )

    monkeypatch.setattr(
        "testing.host.run_cell_verification._poll_execution_time_sec",
        lambda *a, **k: {"execution_time_sec": 0.006, "execution_title": "Cell executed in 0.006s"},
    )

    result = wait_for_run_verification(
        "https://www.kaggle.com/code/u/nb/edit",
        1,
        before,
        host_log_offset=offset,
        timeout=2.0,
    )

    assert result["ok"] is True
    assert result["run_verified"] is True
    assert result["wait_reason"] == "exec_detected_log"
    assert result["execution_order"] == 6
    assert result.get("execution_time_sec") == 0.006


def test_observer_does_not_false_positive_on_unchanged_output(monkeypatch, tmp_path):
    """Stale live scrape with same output must not verify before a real run signal."""
    log_path = tmp_path / "host.log"
    log_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("testing.host.config.LOG_PATH", log_path)
    monkeypatch.setattr("testing.host.cell_structure_live._HOST_LOG_PATH", log_path)

    same_output = "SyntaxError\n"
    before = {
        "cells": [
            {"index": 2, "type": "code", "input": "print(1)", "output": same_output},
        ]
    }
    after = {
        "cells": [
            {"index": 2, "type": "code", "input": "print(1)", "output": same_output},
        ]
    }

    monkeypatch.setattr(
        "testing.host.notebook_context.load_notebook_snapshot",
        lambda url: (after, "live"),
    )
    monkeypatch.setattr(
        "testing.host.run_cell_verification._poll_execution_time_sec",
        lambda *a, **k: {"execution_time_sec": None, "execution_title": None},
    )

    result = wait_for_run_verification(
        "https://www.kaggle.com/code/u/nb/edit",
        2,
        before,
        host_log_offset=0,
        timeout=0.15,
    )
    assert result["run_verified"] is False


def test_capture_run_baseline_includes_offset(monkeypatch, tmp_path):
    from testing.host.run_cell_verification import capture_run_baseline

    log_path = tmp_path / "host.log"
    log_path.write_text("x" * 120, encoding="utf-8")
    monkeypatch.setattr("testing.host.config.LOG_PATH", log_path)

    snap = {"cells": [{"index": 2, "type": "code", "output": ""}]}
    monkeypatch.setattr(
        "testing.host.notebook_context.load_notebook_snapshot",
        lambda url: (snap, "live"),
    )

    baseline = capture_run_baseline("https://www.kaggle.com/code/u/nb/edit", 2)
    assert baseline["host_log_offset"] == 120
    assert baseline["before_cell"]["index"] == 2
