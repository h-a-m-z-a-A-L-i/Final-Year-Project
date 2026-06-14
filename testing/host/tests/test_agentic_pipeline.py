import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_pipeline import (
    init_pipeline_state,
    is_independent_parallel_runs,
    is_sequential_pipeline,
    record_pipeline_run,
)


def test_independent_parallel_prints():
    edits = {3: "print(1)", 4: "print(2)", 5: "print(3)"}
    assert is_independent_parallel_runs(edits) is True
    assert is_sequential_pipeline("print 1,2,3 in cells", edits) is False


def test_sequential_pipeline_import_load():
    edits = {
        3: "import pandas as pd",
        4: "df = pd.read_csv('data.csv')",
        5: "print(df.head())",
    }
    prompt = "import pandas, load the dataset, print top rows"
    assert is_sequential_pipeline(prompt, edits) is True


def test_pipeline_run_advances_queue():
    pipeline = init_pipeline_state("load and print", [3, 4, 5])
    pipeline = record_pipeline_run(
        pipeline,
        3,
        {"ok": True, "run_succeeded": True, "output": "ok\n"},
    )
    assert pipeline["completed_runs"] == [3]
    assert pipeline["pending_runs"] == [4, 5]
    assert pipeline["last_run_cell"] == 3
    assert pipeline["complete"] is False

    pipeline = record_pipeline_run(
        pipeline,
        4,
        {"ok": True, "run_succeeded": True, "output": "rows\n"},
    )
    assert pipeline["pending_runs"] == [5]

    pipeline = record_pipeline_run(
        pipeline,
        5,
        {"ok": True, "run_succeeded": True, "output": "head\n"},
    )
    assert pipeline["complete"] is True
    assert pipeline["pending_runs"] == []
