"""Tests for notebook semantic index layer."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

FIXTURES = Path(__file__).parent / "fixtures"

from testing.host.agent_state import (
    empty_agent_state,
    format_agent_state_block,
    inject_agent_state_message,
    update_agent_state_from_verification,
)
from testing.host.notebook_semantic_index import (
    build_index_from_notebook_file,
    estimate_index_tokens,
    format_semantic_index_block,
    lookup_symbol,
    parse_cell_semantics,
    save_semantic_index,
    sync_semantic_index_to_agent_state,
    update_semantic_index_from_verification,
)


def test_parse_cell_titanic_imports_and_models():
    code = (
        "import pandas as pd\n"
        "from sklearn.ensemble import RandomForestClassifier\n"
        "train_df = pd.read_csv('/kaggle/input/titanic/train.csv')\n"
        "model = RandomForestClassifier()\n"
    )
    sem = parse_cell_semantics(2, code)
    assert "pandas" in sem["imports"] or "pd" in sem["imports"]
    assert "train_df" in sem["dataframes"]
    assert any("RandomForest" in m for m in sem["models"])
    assert "/kaggle/input/titanic/train.csv" in sem["file_paths"]


def test_build_index_from_titanic_fixture():
    idx = build_index_from_notebook_file(FIXTURES / "titanic_notebook.json")
    assert "pd" in idx["imports"] or "pandas" in idx["imports"]
    assert "train_df" in idx["dataframes"]
    assert "test_df" in idx["dataframes"]
    assert any("preprocess_data" in f for f in idx["functions"])
    assert any("RandomForest" in m for m in idx["models"])


def test_build_index_from_cnn_fixture():
    idx = build_index_from_notebook_file(FIXTURES / "cnn_notebook.json")
    assert "torch" in idx["imports"]
    assert "SimpleCNN" in idx["classes"]
    assert "model" in idx["models"]
    assert any("cnn_model.pth" in p for p in idx["file_paths"])


def test_build_index_from_eda_fixture():
    idx = build_index_from_notebook_file(FIXTURES / "eda_notebook.json")
    assert "pd" in idx["imports"] or "pandas" in idx["imports"]
    assert "plt" in idx["imports"] or "matplotlib" in idx["imports"]
    assert "df" in idx["dataframes"]
    assert "corr_df" in idx["dataframes"]


def test_incremental_update_from_verification():
    idx = build_index_from_notebook_file(FIXTURES / "titanic_notebook.json")
    v = {
        "queue_cell_evidence": {
            "cells": [
                {
                    "cell_index": 17,
                    "input": "val_df = test_df.copy()\nmodel.fit(X, val_df)",
                    "output": "ok",
                }
            ]
        },
        "executed": [{"tool": "edit_cell_by_index", "cell_index": 17}],
    }
    updated = update_semantic_index_from_verification(idx, v)
    assert "val_df" in updated["dataframes"]
    recent = updated.get("recent_changes") or []
    assert any(r.get("cell_index") == 17 for r in recent)


def test_format_semantic_index_block():
    idx = build_index_from_notebook_file(FIXTURES / "titanic_notebook.json")
    block = format_semantic_index_block(idx)
    assert "NOTEBOOK STATE" in block
    assert "Imports:" in block
    assert "DataFrames:" in block
    assert "Functions:" in block
    assert "RandomForest" in block or "Models:" in block


def test_agent_state_includes_notebook_state():
    idx = build_index_from_notebook_file(FIXTURES / "titanic_notebook.json")
    state = empty_agent_state(goal="Build Titanic pipeline")
    state = sync_semantic_index_to_agent_state(state, None, notebook_key="titanic-test")
    state["_semantic_index_full"] = idx
    state["notebook_semantic"] = {
        k: idx.get(k) for k in ("imports", "dataframes", "functions", "classes", "models", "variables", "file_paths", "recent_changes")
    }
    block = format_agent_state_block(state)
    assert "NOTEBOOK STATE" in block
    assert "train_df" in block


def test_lookup_symbol_reduces_get_cell_need():
    idx = build_index_from_notebook_file(FIXTURES / "titanic_notebook.json")
    hit = lookup_symbol(idx, "train_df")
    assert hit is not None
    assert hit["kind"] == "dataframe"
    assert hit["cells"]
    hit_fn = lookup_symbol(idx, "preprocess_data")
    assert hit_fn is not None
    assert hit_fn["kind"] == "function"


def test_update_agent_state_syncs_semantic_index(tmp_path):
    mem = tmp_path / "semantic.json"
    with patch("testing.host.notebook_semantic_index.INDEX_PATH", mem):
        with patch("testing.host.notebook_semantic_index.get_active_notebook_key", return_value="nb-1"):
            state = empty_agent_state(goal="EDA")
            v = {
                "verified": True,
                "tool_queue_complete": True,
                "queue_cell_evidence": {
                    "cells": [{"cell_index": 3, "input": "import numpy as np\nx = 1", "output": ""}]
                },
                "executed": [{"tool": "run_cell", "cell_index": 3}],
            }
            out = update_agent_state_from_verification(state, v, goal="EDA")
            assert out.get("notebook_semantic")
            imports = out["notebook_semantic"].get("imports") or []
            assert "np" in imports or "numpy" in imports


def test_inject_bootstraps_semantic_index():
    idx = build_index_from_notebook_file(FIXTURES / "titanic_notebook.json")
    state = empty_agent_state(goal="test")
    state["notebook_key"] = "titanic-test"
    state["_semantic_index_full"] = idx
    state["notebook_semantic"] = {
        k: idx.get(k)
        for k in ("imports", "dataframes", "functions", "classes", "models", "variables", "file_paths", "recent_changes")
    }
    msgs = inject_agent_state_message([{"role": "user", "content": "go"}], state)
    assert any("NOTEBOOK STATE" in str(m.get("content")) for m in msgs if m.get("_react_agent_state"))


def test_index_token_estimate_compact():
    idx = build_index_from_notebook_file(FIXTURES / "titanic_notebook.json")
    tokens = estimate_index_tokens(idx)
    assert tokens < 400
    get_cell_tokens = 800
    assert tokens < get_cell_tokens
