import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.snapshot_verification import (
    apply_execution_metadata_clear,
    evaluate_persistent_update,
)


def _sample(cells):
    return {"tabUrl": "https://example.com/code/u/n/edit", "cells": cells}


def test_initial_create_allowed():
    decision = evaluate_persistent_update(None, _sample([{"index": 1, "input": "x = 1"}]))
    assert decision.allow_write is True
    assert decision.reason == "initial_create"


def test_unchanged_snapshot_blocked():
    existing = _sample([{"index": 1, "input": "x = 1", "output": "", "execution_order": 1}])
    incoming = _sample([{"index": 1, "input": "x = 1", "output": "", "execution_order": 1}])
    decision = evaluate_persistent_update(existing, incoming)
    assert decision.allow_write is False
    assert decision.reason == "unchanged"


def test_content_change_allowed():
    existing = _sample([{"index": 1, "input": "x = 1"}])
    incoming = _sample([{"index": 1, "input": "x = 2"}])
    decision = evaluate_persistent_update(existing, incoming)
    assert decision.allow_write is True
    assert decision.reason == "content_changed"


def test_partial_scrape_blocked():
    existing = _sample(
        [
            {"index": 1, "input": "a"},
            {"index": 2, "input": "b"},
        ]
    )
    incoming = _sample([{"index": 1, "input": "a"}])
    decision = evaluate_persistent_update(existing, incoming)
    assert decision.allow_write is False
    assert decision.reason.startswith("reject_partial_scrape")


def test_empty_incoming_blocked():
    existing = _sample([{"index": 1, "input": "a"}])
    incoming = _sample([])
    decision = evaluate_persistent_update(existing, incoming)
    assert decision.allow_write is False
    assert decision.reason == "reject_empty_incoming"


def test_execution_reset_counts_as_change():
    existing = _sample(
        [
            {
                "index": 1,
                "input": "a",
                "output": "1",
                "execution_order": 3,
                "execution_title": "Execution #3",
            }
        ]
    )
    cleared = apply_execution_metadata_clear(existing)
    decision = evaluate_persistent_update(existing, cleared)
    assert decision.allow_write is True
    assert decision.reason == "content_changed"
