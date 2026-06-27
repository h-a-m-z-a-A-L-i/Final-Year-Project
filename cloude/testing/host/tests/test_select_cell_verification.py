"""Tests for select_cell_verification."""

from __future__ import annotations

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.select_cell_verification import wait_for_select_verification  # noqa: E402


def test_wait_for_select_verification_persistent_skip():
    out = wait_for_select_verification(
        "https://example.com/edit",
        dom_index=1,
        app_index=2,
        timeout=1.0,
    )
    assert out["select_verified"] is True
    assert out["wait_reason"] == "persistent_skip_no_structure_change"
