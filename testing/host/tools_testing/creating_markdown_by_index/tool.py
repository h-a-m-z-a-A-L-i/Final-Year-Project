"""creating_markdown_by_index tool — isolated copy; logic lives in creating_markdown_tool."""

from __future__ import annotations

import sys
from pathlib import Path

_HOST = Path(__file__).resolve().parents[2]
_REPO = _HOST.parents[1]
for path in (str(_REPO), str(_HOST)):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from testing.host.creating_markdown_tool import run_creating_markdown
except Exception:
    from creating_markdown_tool import run_creating_markdown  # type: ignore

TOOL = "creating_markdown_by_index"

__all__ = ["TOOL", "run_creating_markdown"]
