import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.agentic_tool_chain import (
    build_direct_edit_from_prompt,
    build_edit_after_insert,
    extract_cell_content_from_prompt,
    infer_new_cell_index,
    insert_timed_out_but_likely_ok,
)


def test_extract_content():
    assert extract_cell_content_from_prompt("insert a cell below cell 2 with print('hi')") == "print('hi')"
    assert extract_cell_content_from_prompt("add cell with x = 1") == "x = 1"
    assert extract_cell_content_from_prompt("insert below cell 1") is None


def test_infer_new_cell_below():
    args = {"index": 2, "direction": "below"}
    assert infer_new_cell_index(args, {}) == 3
    assert infer_new_cell_index(args, {"new_cell_index": 4}) == 4


def test_build_edit_after_insert_on_timeout():
    args = {"index": 2, "direction": "below", "url": "https://example.com/edit"}
    result = {"ok": False, "error": "timeout"}
    assert insert_timed_out_but_likely_ok(result)
    built = build_edit_after_insert(
        "insert below cell 2 with print('hi')",
        args,
        result,
        url="https://example.com/edit",
    )
    assert built == {
        "cell_index": 3,
        "content": "print('hi')",
        "url": "https://example.com/edit",
    }


def test_build_direct_edit_hamza_prompt():
    built = build_direct_edit_from_prompt(
        'insert this code in cell 1 (print("hamza"))',
        url="https://example.com/edit",
    )
    assert built == {
        "cell_index": 1,
        "content": 'print("hamza")',
        "url": "https://example.com/edit",
    }
