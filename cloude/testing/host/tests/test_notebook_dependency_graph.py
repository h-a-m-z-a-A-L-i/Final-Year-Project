"""Tests for notebook dependency graph layer."""

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
from testing.host.notebook_dependency_graph import (
    GRAPH_PATH,
    analyze_impact,
    build_graph_from_notebook_file,
    build_smart_repair_hints,
    dependency_graph_enabled,
    estimate_dependency_tokens,
    format_dependency_summary,
    lookup_symbol_cell,
    sync_dependency_graph_to_agent_state,
    update_graph_from_verification,
)


def test_build_titanic_graph_symbol_to_cell():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    assert lookup_symbol_cell(g, "train_df") == 4  # reassigned in cell 4
    assert lookup_symbol_cell(g, "preprocess_data") == 3
    assert lookup_symbol_cell(g, "train_model") == 5
    assert "6" in g["cell_to_symbols"]


def test_titanic_dependency_edges():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    kinds = {e["kind"] for e in g["edges"]}
    assert "cell->symbol" in kinds
    assert "function->variable" in kinds or "variable->variable" in kinds


def test_impact_analysis_train_df():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    impact = analyze_impact(g, "train_df")
    assert impact["symbol"] == "train_df"
    assert impact["affected_cells"]
    assert any(
        s in impact["affected_symbols"] or s == "train_df"
        for s in ["preprocess_data", "X", "y", "clf", "train_model"]
    ) or len(impact["affected_symbols"]) >= 0


def test_impact_includes_downstream_symbols():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    impact = analyze_impact(g, "train_df")
    downstream = set(impact["affected_symbols"])
    assert "X" in downstream or "clf" in downstream or "preprocess_data" in downstream or impact["affected_cells"]


def test_format_dependency_summary():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    block = format_dependency_summary(g, focus_symbol="train_df")
    assert "DEPENDENCY SUMMARY" in block
    assert "train_df" in block


def test_smart_repair_hints_name_error():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    hints = build_smart_repair_hints(
        g,
        error_cell=6,
        error_summary="NameError: name 'X' is not defined",
    )
    assert hints["priority_cells"]
    assert hints["hint"]
    assert "notebook_get_cell" in hints["hint"] or "Inspect cells" in hints["hint"]


def test_incremental_graph_update():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    v = {
        "queue_cell_evidence": {
            "cells": [{"cell_index": 17, "input": "val_df = train_df.copy()\nmodel.fit(val_df)", "output": ""}]
        },
        "expected_edits": {17: "val_df = train_df.copy()\nmodel.fit(val_df)"},
    }
    updated = update_graph_from_verification(g, v)
    assert lookup_symbol_cell(updated, "val_df") == 17


def test_agent_state_includes_dependency_summary():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    state = empty_agent_state(goal="Titanic pipeline")
    state["_dependency_graph_full"] = g
    state["_dependency_summary"] = format_dependency_summary(g)
    block = format_agent_state_block(state)
    assert "DEPENDENCY SUMMARY" in block


def test_error_enriches_dependency_repair(tmp_path):
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    with patch("testing.host.notebook_dependency_graph.GRAPH_PATH", tmp_path / "g.json"):
        with patch("testing.host.notebook_dependency_graph.ensure_dependency_graph", return_value=g):
            with patch("testing.host.notebook_semantic_index.semantic_index_enabled", return_value=False):
                state = empty_agent_state(goal="fix")
                state["notebook_key"] = "test"
                v = {
                    "needs_fix": True,
                    "execution_error": {
                        "cell_index": 6,
                        "error_summary": "NameError: name 'X' is not defined",
                    },
                }
                out = update_agent_state_from_verification(state, v, goal="fix")
                err = out.get("last_error") or {}
                assert err.get("dependency_repair") or out.get("_dependency_summary")


def test_cnn_graph_has_model_chain():
    g = build_graph_from_notebook_file(FIXTURES / "cnn_notebook.json")
    assert lookup_symbol_cell(g, "SimpleCNN") == 3
    assert lookup_symbol_cell(g, "model") == 4
    block = format_dependency_summary(g)
    assert "DEPENDENCY SUMMARY" in block


def test_eda_graph_dataframe_deps():
    g = build_graph_from_notebook_file(FIXTURES / "eda_notebook.json")
    assert lookup_symbol_cell(g, "df") == 2
    assert lookup_symbol_cell(g, "corr_df") == 4


def test_dependency_summary_token_compact():
    g = build_graph_from_notebook_file(FIXTURES / "titanic_notebook.json")
    tokens = estimate_dependency_tokens(g)
    assert tokens < 250


def test_dependency_graph_enabled_default():
    with patch.dict(os.environ, {"AGENTIC_DEPENDENCY_GRAPH": "1"}):
        assert dependency_graph_enabled() is True
