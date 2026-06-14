"""Tests for Cerebras static notebook baseline + delta caching."""

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.prompt_cache_baseline import (
    compute_notebook_delta,
    effective_session_id,
)


def test_effective_session_id_adds_mode_when_enabled():
    import testing.host.config as cfg

    prev = cfg.CHAT_SESSION_PER_MODE
    try:
        cfg.CHAT_SESSION_PER_MODE = True
        assert effective_session_id("abc", "ask") == "abc::mode::ask"
        assert effective_session_id("abc", "agentic") == "abc::mode::agentic"
    finally:
        cfg.CHAT_SESSION_PER_MODE = prev


def test_effective_session_id_unchanged_by_default():
    import testing.host.config as cfg

    prev = cfg.CHAT_SESSION_PER_MODE
    try:
        cfg.CHAT_SESSION_PER_MODE = False
        assert effective_session_id("abc", "ask") == "abc"
    finally:
        cfg.CHAT_SESSION_PER_MODE = prev


def test_compute_notebook_delta_detects_input_and_output_change():
    baseline = [
        {"index": 1, "type": "code", "input": "a", "output": "", "execution_order": None},
        {"index": 2, "type": "code", "input": "x", "output": "1", "execution_order": 1},
    ]
    live = [
        {"index": 1, "type": "code", "input": "a", "output": "", "execution_order": None},
        {"index": 2, "type": "code", "input": "x", "output": "2", "execution_order": 2},
        {"index": 3, "type": "code", "input": "new", "output": "", "execution_order": None},
    ]
    delta = compute_notebook_delta(baseline, live)
    assert "Changed cells" in delta
    assert "New cells" in delta
    assert '"cell_index": 2' in delta
    assert '"cell_index": 3' in delta


def test_compute_notebook_delta_empty_when_unchanged():
    cells = [{"index": 1, "type": "code", "input": "a", "output": "b", "execution_order": 1}]
    assert compute_notebook_delta(cells, cells) == ""
