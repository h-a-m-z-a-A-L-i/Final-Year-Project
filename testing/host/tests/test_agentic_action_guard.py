import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_action_guard import (
    is_actionable_notebook_request,
    is_run_verify_request,
    is_write_only_request,
    looks_like_instruction_only_response,
    parse_last_n_cells_request,
    resolve_wanted_run_cells,
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
