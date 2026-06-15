import os
import sys
from pathlib import Path

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_action_guard import (
    batch_has_split_source_read,
    build_split_cell_source_nudge,
    is_actionable_notebook_request,
    is_run_verify_request,
    is_write_only_request,
    looks_like_instruction_only_response,
    parse_last_n_cells_request,
    prompt_requests_split_cell,
    resolve_wanted_run_cells,
    split_cell_write_without_source,
    user_requests_run,
)
from testing.host.agentic_batch_executor import ParsedToolCall, enrich_run_cells_from_prompt


def test_instruction_only_detection():
    text = (
        "Placement\n\nInsert a new code cell directly below Cell 14.\n"
        "Run order: first execute Cell 13, then Cell 14.\n"
        "Code\n\npython\n\ndf.head()"
    )
    assert looks_like_instruction_only_response(text) is True


def test_write_only_vs_run_verify():
    write_prompt = (
        "write code to import /kaggle/input/x.csv and print its top rows, "
        "then in next cell remove empty values"
    )
    run_prompt = "now run these cells and verify the output"
    assert is_write_only_request(write_prompt) is True
    assert user_requests_run(run_prompt) is True
    assert is_run_verify_request(run_prompt) is True
    assert is_actionable_notebook_request(write_prompt) is True


def test_parse_last_n_cells_request():
    assert parse_last_n_cells_request("run last 3 cells of this notebook") == 3
    assert parse_last_n_cells_request("execute the last 5 code cells") == 5
    assert parse_last_n_cells_request("run cell 3") is None


def test_resolve_wanted_run_cells_last_n():
    registry = type("R", (), {})()
    registry.call = lambda name, args: {
        "cells": [
            {"index": 20, "type": "markdown"},
            {"index": 21, "type": "code"},
            {"index": 22, "type": "code"},
            {"index": 23, "type": "code"},
            {"index": 24, "type": "code"},
            {"index": 25, "type": "code"},
        ]
    }
    calls = [
        ParsedToolCall("1", "run_cell", {"cell_index": 23, "url": "https://x/edit"}),
    ]
    wanted = resolve_wanted_run_cells(
        "run last 3 cells",
        calls,
        registry=registry,
        url="https://x/edit",
    )
    assert wanted == [23, 24, 25]


def test_enrich_run_cells_from_prompt_expands_queue():
    registry = type("R", (), {})()
    registry.call = lambda name, args: {
        "cells": [
            {"index": 23, "type": "code"},
            {"index": 24, "type": "code"},
            {"index": 25, "type": "code"},
        ]
    }
    calls = [
        ParsedToolCall("1", "notebook_list_cells", {"url": "https://x/edit"}),
        ParsedToolCall("2", "run_cell", {"cell_index": 23, "url": "https://x/edit"}),
    ]
    out = enrich_run_cells_from_prompt(
        calls,
        user_prompt="run last 3 cells of this notebook",
        url="https://x/edit",
        registry=registry,
    )
    run_indices = [c.args["cell_index"] for c in out if c.name == "run_cell"]
    assert run_indices == [23, 24, 25]


def test_resolve_wanted_run_cells_explicit_cell_overrides_last_n():
    registry = type("R", (), {})()
    registry.call = lambda name, args: {
        "cells": [{"index": i, "type": "code"} for i in range(20, 31)]
    }
    calls = [
        ParsedToolCall("1", "run_cell", {"cell_index": 26, "url": "https://x/edit"}),
    ]
    wanted = resolve_wanted_run_cells(
        "Fix the error in cell 30 and run it after fixing",
        calls,
        registry=registry,
        url="https://x/edit",
    )
    assert wanted == [30]


def test_enrich_run_cells_replaces_wrong_model_run_with_prompt_cell():
    registry = type("R", (), {})()
    registry.call = lambda name, args: {"cells": []}
    calls = [
        ParsedToolCall("a", "edit_cell_by_index", {"cell_index": 30, "content": "x", "url": "https://x/edit"}),
        ParsedToolCall("b", "run_cell", {"cell_index": 26, "url": "https://x/edit"}),
    ]
    out = enrich_run_cells_from_prompt(
        calls,
        user_prompt="Fix cell 30 and run it",
        url="https://x/edit",
        registry=registry,
    )
    run_indices = [c.args["cell_index"] for c in out if c.name == "run_cell"]
    assert run_indices == [30]


def test_prompt_requests_split_cell_detects_divide_and_refactor():
    assert prompt_requests_split_cell("Split cell 38 into 3 smaller cells")
    assert prompt_requests_split_cell("divide the code in cell 38 into separate cells")
    assert prompt_requests_split_cell("refactor cell 12 into smaller pieces")
    assert not prompt_requests_split_cell("create 3 new cells under cell 38")


def test_split_cell_write_without_source_requires_get_cell():
    prompt = "Split cell 38 into 3 smaller cells"
    writes = [
        ParsedToolCall("1", "insert_cell", {"index": 38, "direction": "below"}),
        ParsedToolCall("2", "edit_cell_by_index", {"cell_index": 39, "content": "x"}),
    ]
    assert split_cell_write_without_source(prompt, writes) is True
    reads = [
        ParsedToolCall("1", "notebook_get_cell", {"cell_index": 38}),
        ParsedToolCall("2", "insert_cell", {"index": 38, "direction": "below"}),
    ]
    assert batch_has_split_source_read(prompt, reads) is True
    assert split_cell_write_without_source(prompt, reads) is False


def test_build_split_cell_source_nudge_mentions_placeholders():
    nudge = build_split_cell_source_nudge("Split cell 38 into 3 smaller cells")
    assert "notebook_get_cell" in nudge
    assert "print(1)" in nudge


def test_agentic_prompt_contains_split_cell_few_shot():
    path = Path(__file__).resolve().parents[1] / "prompts" / "agentic.txt"
    text = path.read_text(encoding="utf-8")
    assert "Split cell 38 into 3 smaller cells" in text
    assert "NEVER use `print(1)`" in text
    assert "notebook_get_cell" in text
