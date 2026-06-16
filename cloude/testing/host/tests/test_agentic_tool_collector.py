import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_tool_collector import expected_tool_floor, collection_satisfied


def test_expected_tool_floor_complex_prompt():
    prompt = "insert below 2, edit 3, insert below 3, edit 4, run cell 3, run cell 4"
    assert expected_tool_floor(prompt) >= 6


def test_collection_satisfied_needs_all_runs():
    prompt = "run last 3 cells"
    calls = [{"function": {"name": "run_cell", "arguments": '{"cell_index":1}'}}]
    assert collection_satisfied(prompt, calls) is False
