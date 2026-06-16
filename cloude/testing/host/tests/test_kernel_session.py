"""Tests for kernel session tracking."""

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.kernel_session import (
    analyze_kernel_session,
    classify_cell_session_status,
    suggest_prerequisite_runs,
)
from testing.host.local_notebook_tools import notebook_kernel_state


def _cells():
    return [
        {
            "type": "code",
            "index": 2,
            "input": "import pandas as pd\ndf = pd.read_csv('/kaggle/input/housing/data.csv')",
            "output": "shape (1000, 10)\n",
            "execution_order": None,
            "execution_title": "",
        },
        {
            "type": "code",
            "index": 30,
            "input": "lahore = df[df['city']=='Lahore']\nprint(lahore.head())",
            "output": "Empty DataFrame\n",
            "execution_order": 3,
            "execution_title": "Execution #3",
        },
    ]


def test_stale_output_after_fresh_kernel():
    cell = _cells()[0]
    assert (
        classify_cell_session_status(
            cell,
            kernel_scenario="scenario_2_fresh_kernel_started",
            revision_flags={},
        )
        == "stale_output"
    )
    assert (
        classify_cell_session_status(
            _cells()[1],
            kernel_scenario="scenario_2_fresh_kernel_started",
            revision_flags={30: {"seen_running": True}},
        )
        == "ran_this_session"
    )
    # execution_order without seen_running after fresh → stale (DOM metadata)
    assert (
        classify_cell_session_status(
            _cells()[0],
            kernel_scenario="scenario_2_fresh_kernel_started",
            revision_flags={2: {"seen_running": False}},
        )
        == "stale_output"
    )


def test_analyze_kernel_session_fresh_lists_stale_data_load(enable_execution_metadata):
    report = analyze_kernel_session(
        "https://www.kaggle.com/code/test/edit",
        _cells(),
        target_cell_index=30,
        symbols=["df"],
    )
    assert report["kernel_scenario_label"] in {"fresh", "off", "unknown", "reload"}
    assert any(c["index"] == 2 for c in report["stale_data_load_cells"])
    assert 2 in report["suggested_prerequisite_runs"]


def test_notebook_kernel_state_tool(enable_execution_metadata):
    import json
    from pathlib import Path

    url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
    path = Path(__file__).resolve().parents[1] / "data" / "notebooks" / (
        "https___www_kaggle_com_code_codekey_testing_ol_edit.json"
    )
    if not path.is_file():
        return
    result = notebook_kernel_state({"url": url})
    assert result.get("ok") is True
    assert "summary" in result
    assert "ran_this_session" in result
