"""Tests for notebook runtime state awareness."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

FIXTURES = Path(__file__).parent / "fixtures"

from testing.host.agent_state import (
    empty_agent_state,
    format_agent_state_block,
    update_agent_state_from_verification,
)
from testing.host.runtime_state import (
    RUNTIME_PATH,
    build_error_runtime_context,
    build_runtime_from_notebook_file,
    estimate_runtime_tokens,
    extract_output_facts,
    format_runtime_state_block,
    runtime_state_enabled,
    sync_runtime_state_to_agent_state,
    update_runtime_from_verification,
)


def test_extract_shape_from_output():
    facts = extract_output_facts(3, "(891, 12)", code="print(train_df.shape)")
    assert facts["dataframes"].get("train_df", {}).get("shape") == [891, 12]


def test_extract_accuracy_metric():
    facts = extract_output_facts(18, "accuracy=0.82\n", code="print accuracy")
    assert any(m["name"] == "accuracy" and m["value"] == 0.82 for m in facts["metrics"])


def test_build_runtime_from_titanic_fixture():
    rt = build_runtime_from_notebook_file(FIXTURES / "titanic_runtime_notebook.json")
    assert rt["dataframes"]["train_df"]["shape"] == [891, 12]
    assert rt["models"]
    acc_models = [m for m in rt["metrics"] if m["name"] == "accuracy"]
    assert acc_models and acc_models[0]["value"] == 0.82


def test_format_runtime_under_200_tokens():
    rt = build_runtime_from_notebook_file(FIXTURES / "titanic_runtime_notebook.json")
    block = format_runtime_state_block(rt)
    assert "RUNTIME STATE" in block
    assert "891x12" in block or "891" in block
    assert estimate_runtime_tokens(rt) <= 200


def test_error_runtime_context():
    rt = build_runtime_from_notebook_file(FIXTURES / "titanic_runtime_notebook.json")
    ctx = build_error_runtime_context(rt, error_cell=6, related_symbols=["train_df"])
    assert ctx["relevant"]
    assert any("891" in r or "accuracy" in r for r in ctx["relevant"])


def test_agent_state_includes_runtime_only_facts():
    rt = build_runtime_from_notebook_file(FIXTURES / "titanic_runtime_notebook.json")
    state = empty_agent_state(goal="Improve accuracy")
    state["_runtime_state_full"] = rt
    state["_runtime_summary"] = format_runtime_state_block(rt)
    block = format_agent_state_block(state)
    assert "RUNTIME STATE" in block
    assert "IMPROVEMENT TASK" not in block
    rt = build_runtime_from_notebook_file(FIXTURES / "titanic_runtime_notebook.json")
    v = {
        "queue_cell_evidence": {
            "cells": [{"cell_index": 20, "input": "print(test_df.shape)", "output": "(418, 11)"}]
        }
    }
    updated = update_runtime_from_verification(rt, v)
    assert updated["dataframes"]["test_df"]["shape"] == [418, 11]


def test_incremental_runtime_update():
    rt = build_runtime_from_notebook_file(FIXTURES / "titanic_runtime_notebook.json")
    with patch("testing.host.runtime_state.ensure_runtime_state", return_value=rt):
        with patch("testing.host.runtime_state.save_runtime_state", lambda *a, **k: None):
            with patch("testing.host.notebook_semantic_index.semantic_index_enabled", return_value=False):
                with patch("testing.host.notebook_dependency_graph.dependency_graph_enabled", return_value=False):
                    state = empty_agent_state(goal="fix")
                    state["notebook_key"] = "test-key"
                    v = {
                        "needs_fix": True,
                        "execution_error": {
                            "cell_index": 6,
                            "error_summary": "ValueError: shape mismatch",
                        },
                        "queue_cell_evidence": {
                            "cells": [
                                {
                                    "cell_index": 6,
                                    "input": "acc = model.score()",
                                    "output": "accuracy=0.82\n",
                                }
                            ]
                        },
                    }
                    out = update_agent_state_from_verification(state, v, goal="fix")
                    err = out.get("last_error") or {}
                    assert err.get("runtime_context") or "RUNTIME STATE" in format_agent_state_block(out)


def test_pakistan_housing_shapes_from_fixture():
    path = FIXTURES.parent.parent / "data" / "notebooks" / "https___www_kaggle_com_code_codekey_pakistan_housing_edit.json"
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "data" / "notebooks" / "https___www_kaggle_com_code_codekey_pakistan_housing_edit.json"
    if path.is_file():
        rt = build_runtime_from_notebook_file(path)
        assert rt["dataframes"] or rt["recent_outputs"]


def test_runtime_enabled_default():
    with patch.dict(os.environ, {"AGENTIC_RUNTIME_STATE": "1"}):
        assert runtime_state_enabled() is True
