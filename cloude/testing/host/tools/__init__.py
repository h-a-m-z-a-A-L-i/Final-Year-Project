"""Tools package for isolated bot clients.

This package contains copies of interactive tools (insert_cell, etc.)
that run from a contained `tools` folder to aid debugging.
"""

from pathlib import Path
from importlib import import_module

# Expose available tool modules for programmatic use by the tool registry.
__all__ = [
	"insert_cell",
	"edit_cell_by_index",
	"delete_by_index",
	"select_cell_by_index",
	"creating_markdown_by_index",
]

# Lazy imports: make the modules available as attributes when package is imported
_pkg_dir = Path(__file__).resolve().parent
for mod_name in list(__all__):
	try:
		# Import as package.module if possible, otherwise fall back to file import by name
		globals()[mod_name] = import_module(f"testing.host.tools.{mod_name}")
	except Exception:
		try:
			globals()[mod_name] = import_module(mod_name)
		except Exception:
			globals()[mod_name] = None


# Also expose the library-style adapter functions from testing.host.tool_adapters
# so callers can use testing.host.tools.click_cell(...) directly as a function.
try:
	from ..tool_adapters import (
		insert_cell as insert_cell_func,
		edit_cell_by_index as edit_cell_by_index_func,
		delete_by_index as delete_by_index_func,
		select_cell_by_index as select_cell_by_index_func,
		notebook_graph_query as notebook_graph_query_func,
	)
	# Add function names into module globals for convenience
	globals()["insert_cell"] = insert_cell_func
	globals()["edit_cell_by_index"] = edit_cell_by_index_func
	globals()["delete_by_index"] = delete_by_index_func
	globals()["select_cell_by_index"] = select_cell_by_index_func
	globals()["notebook_graph_query"] = notebook_graph_query_func
	__all__.extend(["notebook_graph_query"])
except Exception:
	# If adapters aren't available, skip — tools modules still present
	pass
