"""watch_notebook_json — print persistent JSON cell changes to terminal."""

from __future__ import annotations

import sys
from pathlib import Path

_HOST = Path(__file__).resolve().parents[2]
_REPO = _HOST.parents[1]
for path in (str(_REPO), str(_HOST)):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from testing.host.persistent_json_watch import run_watch_notebook_json
except Exception:
    from persistent_json_watch import run_watch_notebook_json  # type: ignore

TOOL = "watch_notebook_json"

__all__ = ["TOOL", "run_watch_notebook_json"]
