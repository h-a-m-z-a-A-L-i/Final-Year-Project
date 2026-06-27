"""insert_cell tool — isolated copy; verification logic lives in insert_cell_tool."""

from __future__ import annotations

import sys
from pathlib import Path

_HOST = Path(__file__).resolve().parents[2]
_REPO = _HOST.parents[1]
for path in (str(_REPO), str(_HOST)):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from testing.host.insert_cell_tool import run_insert_cell
except Exception:
    from insert_cell_tool import run_insert_cell  # type: ignore

TOOL = "insert_cell"

__all__ = ["TOOL", "run_insert_cell"]
