import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST_DIR = HERE.parent
sys.path.insert(0, str(HOST_DIR))

from prompt_utils import _build_profile_memory_context, _extract_cell_number, _extract_user_profile_facts


def test_extract_cell_number():
    assert _extract_cell_number("explain cell 1") == 1
    assert _extract_cell_number("cell#2") == 2
    assert _extract_cell_number("cell3") == 3
    assert _extract_cell_number("cell 0") == 0
    assert _extract_cell_number("dependencies for cell 5") == 5
    assert _extract_cell_number("what does the first cell do") == 0
    assert _extract_cell_number("3rd cell upstream") == 3
    assert _extract_cell_number("index 4 error") == 4
    assert _extract_cell_number("fix last cell") is None


def test_resolve_last_cell_with_error():
    from prompt_utils import resolve_cell_index

    cells = [
        {"index": 24, "type": "code", "input": "x=1", "output": "1"},
        {"index": 25, "type": "code", "input": "bad", "output": "KeyError: 'price'"},
        {"index": 26, "type": "code", "input": "", "output": ""},
    ]
    assert resolve_cell_index("fix last cell, it has error", cells) == 25
    assert resolve_cell_index("edit cell 24", cells) == 24


def test_profile_fact_extraction_and_context():
    facts = _extract_user_profile_facts("my name is Alice")
    assert facts == {"name": "Alice"}

    context = _build_profile_memory_context(facts)
    assert "Alice" in context