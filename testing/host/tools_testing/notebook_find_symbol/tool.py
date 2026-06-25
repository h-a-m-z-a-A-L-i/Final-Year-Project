"""Isolated local tool: notebook_find_symbol (from local_notebook_tools)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from testing.host.local_notebook_tools import notebook_find_symbol as run_tool

TOOL = "notebook_find_symbol"
