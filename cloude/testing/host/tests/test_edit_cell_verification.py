"""Tests for edit_cell dispatch + verify."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.edit_cell_tool import run_edit_cell
from testing.host.edit_cell_verification import (
    capture_edit_baseline,
    wait_for_edit_verification,
)


def test_capture_edit_baseline():
    snap = {
        "cells": [
            {"index": 2, "type": "code", "input": "print(1)"},
        ]
    }
    with patch("testing.host.persistent_notebook_verify.load_persistent_notebook_snapshot", return_value=(snap, "persistent")):
        baseline = capture_edit_baseline("https://example.com/edit", 2)
    assert baseline["before_input"] == "print(1)"
    assert baseline["before_hash"]


def test_wait_for_edit_verification_snapshot():
    after = {"cells": [{"index": 2, "type": "code", "input": "print(42)"}]}

    def _fake_poll(_url, *, timeout, on_tick, poll_sec=None):
        hit = on_tick(after, "hash2", 2.0)
        return hit if hit else {"ok": False, "error": "timeout"}

    with patch("testing.host.persistent_notebook_verify.poll_persistent_snapshot", side_effect=_fake_poll):
        out = wait_for_edit_verification(
            "https://example.com/edit",
            2,
            "print(42)",
            before_input="old",
            dom_index=1,
            timeout=1.0,
        )
    assert out["edit_verified"] is True
    assert out["wait_reason"] == "persistent_snapshot_input"


def test_run_edit_cell_success():
    with patch("testing.host.edit_cell_tool.dispatch_edit_cell") as mock_dispatch:
        mock_dispatch.return_value = {"ok": True, "result": {"ok": True, "dispatched": True}}
        out = run_edit_cell(
            {
                "url": "https://example.com/edit",
                "cell_index": 2,
                "content": "print(42)",
            }
        )
    assert out["ok"] is True
    assert out["dispatched"] is True
    assert out["phase"] == "dispatched"
    assert out["cell_index"] == 2


def test_run_edit_cell_dispatch_failed():
    with patch("testing.host.edit_cell_tool.dispatch_edit_cell") as mock_dispatch:
        mock_dispatch.return_value = {"ok": False, "error": "dispatch failed"}
        out = run_edit_cell(
            {
                "url": "https://example.com/edit",
                "cell_index": 2,
                "content": "print(42)",
            }
        )
    assert out["ok"] is False
    assert out["phase"] == "edit_dispatch_failed"
