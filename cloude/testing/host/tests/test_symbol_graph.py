import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.symbol_graph import build_symbol_index, resolve_symbols_for_cell, pack_target_with_symbols


CELLS = [
    {"index": 1, "type": "code", "input": "import pandas as pd\nBIG_UNRELATED = 1\n" + ("# noise\n" * 50)},
    {"index": 2, "type": "code", "input": "df = pd.read_csv('data.csv')\n"},
    {"index": 3, "type": "code", "input": "print(df.head())\n"},
]


def test_resolve_df_only_from_cell_2():
    index = build_symbol_index(CELLS)
    resolved = resolve_symbols_for_cell(index, 3)
    assert "df" in resolved
    assert resolved["df"].cell_index == 2
    assert "BIG_UNRELATED" not in resolved


def test_pack_excludes_noise_from_cell_1():
    body, sites = pack_target_with_symbols(CELLS, 3, "### Cell [3]\n```python\nprint(df.head())\n```")
    assert "df = pd.read_csv" in body
    assert "BIG_UNRELATED" not in body
    assert any("2:df" in s for s in sites)
