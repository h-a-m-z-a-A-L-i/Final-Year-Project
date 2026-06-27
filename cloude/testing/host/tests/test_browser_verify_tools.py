"""Tests for browser_verify_tools entry points."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.browser_verify_tools import (  # noqa: E402
    run_verify_edit_cell,
    run_verify_insert_cell,
    run_verify_select_cell,
)


def test_run_verify_edit_cell_success():
    with patch(
        "testing.host.browser_verify_tools.wait_for_edit_verification",
        return_value={"ok": True, "edit_verified": True, "wait_reason": "snapshot_input"},
    ):
        out = run_verify_edit_cell(
            {
                "url": "https://example.com/edit",
                "cell_index": 2,
                "content": "print(1)",
            }
        )
    assert out["ok"] is True
    assert out["edit_verified"] is True


def test_run_verify_select_cell_missing_cell():
    out = run_verify_select_cell({"url": "https://example.com/edit"})
    assert out["ok"] is False
    assert "cell_index" in out["error"]


def test_run_verify_insert_cell_requires_before():
    out = run_verify_insert_cell(
        {
            "url": "https://example.com/edit",
            "index": 2,
        }
    )
    assert out["ok"] is False
    assert "before snapshot" in out["error"]
