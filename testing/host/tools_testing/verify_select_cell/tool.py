"""verify_select_cell — poll DOM until cell is active."""

from __future__ import annotations

import sys
from pathlib import Path

_HOST = Path(__file__).resolve().parents[2]
_REPO = _HOST.parents[1]
for path in (str(_REPO), str(_HOST)):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from testing.host.browser_verify_tools import run_verify_select_cell
except Exception:
    from browser_verify_tools import run_verify_select_cell  # type: ignore

TOOL = "verify_select_cell"

__all__ = ["TOOL", "run_verify_select_cell"]
