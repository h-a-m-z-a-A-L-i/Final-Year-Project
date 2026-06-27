"""Tests for insert/delete/markdown cell verification modules."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.creating_markdown_verification import (  # noqa: E402
    capture_markdown_baseline,
    wait_for_markdown_verification,
)
from testing.host.delete_cell_verification import (  # noqa: E402
    capture_delete_baseline,
    wait_for_delete_verification,
)
from testing.host.insert_cell_verification import (  # noqa: E402
    capture_insert_baseline,
    wait_for_insert_verification,
)


def test_capture_insert_baseline():
    snap = {"cells": [{"index": 1, "type": "code", "input": "a"}, {"index": 2, "type": "code", "input": "b"}]}
    with patch("testing.host.persistent_notebook_verify.load_persistent_notebook_snapshot", return_value=(snap, "persistent")):
        baseline = capture_insert_baseline("https://example.com/edit", 2)
    assert baseline["anchor_index"] == 2
    assert baseline["expected_new_index"] == 3
    assert baseline["cell_count"] == 2


def test_wait_for_insert_verification_ok():
    before = {"cells": [{"index": 1, "type": "code", "input": "a"}]}
    after = {
        "cells": [
            {"index": 1, "type": "code", "input": "a"},
            {"index": 2, "type": "code", "input": ""},
        ]
    }

    def _fake_poll(_url, *, timeout, on_tick, poll_sec=None):
        return on_tick(after, "hash2", 2.0) or {"ok": False, "error": "timeout"}

    with patch("testing.host.persistent_notebook_verify.poll_persistent_snapshot", side_effect=_fake_poll):
        out = wait_for_insert_verification(
            "https://example.com/edit",
            before,
            expected_new_index=2,
            anchor_index=1,
            timeout=1.0,
        )
    assert out["insert_verified"] is True
    assert out["expected_new_index"] == 2


def test_wait_for_delete_verification_ok():
    before = {
        "cells": [
            {"index": 1, "type": "code", "input": "a"},
            {"index": 2, "type": "code", "input": "b"},
        ]
    }
    after = {"cells": [{"index": 1, "type": "code", "input": "a"}]}

    def _fake_poll(_url, *, timeout, on_tick, poll_sec=None):
        return on_tick(after, "hash2", 2.0) or {"ok": False, "error": "timeout"}

    with patch("testing.host.persistent_notebook_verify.poll_persistent_snapshot", side_effect=_fake_poll):
        out = wait_for_delete_verification(
            "https://example.com/edit",
            before,
            2,
            timeout=1.0,
        )
    assert out["delete_verified"] is True
    assert out["removed_indices"] == [2]


def test_wait_for_markdown_verification_ok():
    before = {"cells": [{"index": 1, "type": "code", "input": "a"}]}
    after = {
        "cells": [
            {"index": 1, "type": "code", "input": "a"},
            {"index": 2, "type": "markdown", "input": "# Title"},
        ]
    }

    def _fake_poll(_url, *, timeout, on_tick, poll_sec=None):
        return on_tick(after, "hash2", 2.0) or {"ok": False, "error": "timeout"}

    with patch("testing.host.persistent_notebook_verify.poll_persistent_snapshot", side_effect=_fake_poll):
        out = wait_for_markdown_verification(
            "https://example.com/edit",
            before,
            expected_index=2,
            anchor_index=1,
            timeout=1.0,
        )
    assert out["markdown_verified"] is True
    assert out["expected_index"] == 2


def test_capture_delete_baseline():
    snap = {"cells": [{"index": 2, "type": "code", "input": "x"}]}
    with patch("testing.host.persistent_notebook_verify.load_persistent_notebook_snapshot", return_value=(snap, "persistent")):
        baseline = capture_delete_baseline("https://example.com/edit", 2)
    assert baseline["cell_index"] == 2
    assert baseline["cell_count"] == 1


def test_capture_markdown_baseline():
    snap = {"cells": [{"index": 1, "type": "code", "input": "a"}]}
    with patch("testing.host.persistent_notebook_verify.load_persistent_notebook_snapshot", return_value=(snap, "persistent")):
        baseline = capture_markdown_baseline("https://example.com/edit", 1)
    assert baseline["expected_new_index"] == 2
