"""Tests for verification_suite orchestration."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.verification_suite import run_dispatch_and_verify  # noqa: E402


def test_run_dispatch_and_verify_success():
    with patch(
        "testing.host.verification_suite.capture_edit_baseline",
        return_value={"snapshot": {"cells": []}, "before_input": "", "before_hash": ""},
    ), patch(
        "testing.host.edit_cell_tool.run_edit_cell",
        return_value={"ok": True, "dispatched": True},
    ), patch(
        "testing.host.browser_verify_tools.run_verify_edit_cell",
        return_value={"ok": True, "edit_verified": True},
    ):
        out = run_dispatch_and_verify(
            "edit_cell_by_index",
            {
                "url": "https://example.com/edit",
                "cell_index": 2,
                "content": "x = 1",
            },
        )
    assert out["ok"] is True
    assert out["phase"] == "verified"
    assert out["dispatch"]["ok"] is True
    assert out["verify"]["ok"] is True


def test_run_dispatch_and_verify_dispatch_failed():
    with patch(
        "testing.host.verification_suite.capture_run_baseline",
        return_value={"snapshot": {"cells": []}, "host_log_offset": 0},
    ), patch(
        "testing.host.run_cell_tool.run_run_cell",
        return_value={"ok": False, "error": "dispatch failed"},
    ):
        out = run_dispatch_and_verify(
            "run_cell",
            {"url": "https://example.com/edit", "cell_index": 1},
        )
    assert out["ok"] is False
    assert out["phase"] == "dispatch"
