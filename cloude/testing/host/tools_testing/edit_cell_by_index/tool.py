"""edit_cell_by_index tool — isolated copy; fire-and-forget dispatch only."""

from __future__ import annotations

import sys
from pathlib import Path

_HOST = Path(__file__).resolve().parents[2]
_REPO = _HOST.parents[1]
for path in (str(_REPO), str(_HOST)):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from testing.host.edit_cell_tool import run_edit_cell
except Exception:
    from edit_cell_tool import run_edit_cell  # type: ignore

TOOL = "edit_cell_by_index"

__all__ = ["TOOL", "run_edit_cell"]
