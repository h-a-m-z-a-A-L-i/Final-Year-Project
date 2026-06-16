import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.cell_index import app_to_dom, dom_to_app, is_valid_app_index, normalize_notebook_cells


def test_dom_app_round_trip():
    assert dom_to_app(0) == 1
    assert app_to_dom(1) == 0
    assert dom_to_app(4) == 5
    assert app_to_dom(5) == 4


def test_is_valid_app_index():
    assert is_valid_app_index(1)
    assert not is_valid_app_index(0)
    assert not is_valid_app_index(-1)


def test_normalize_legacy_zero_based_cells():
    cells = [
        {"index": 0, "type": "markdown"},
        {"index": 1, "type": "code"},
        {"index": 2, "type": "code"},
    ]
    normalize_notebook_cells(cells)
    assert [c["index"] for c in cells] == [1, 2, 3]


def test_normalize_leaves_one_based_cells():
    cells = [{"index": 1}, {"index": 2}]
    normalize_notebook_cells(cells)
    assert [c["index"] for c in cells] == [1, 2]
