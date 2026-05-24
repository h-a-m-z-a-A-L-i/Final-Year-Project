"""Library-style adapters for tools.

These provide simple programmatic functions that call the registered tools via
the `tool_registry`. They offer a stable Python API for orchestration and tests.
"""
from typing import Dict, Any


def _registry():
    # Resolve registry at call-time to avoid import-time capture and allow
    # tests or runtime to patch config without module reloads.
    from . import tool_registry as _tr
    return _tr.registry()


def click_cell(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("click_cell", args)


def insert_cell(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("insert_cell", args)


def edit_cell_by_index(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("edit_cell_by_index", args)


def delete_by_index(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("delete_by_index", args)


def select_cell_by_index(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("select_cell_by_index", args)


def notebook_graph_query(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("notebook_graph_query", args)


def notebook_snapshot_status(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("notebook_snapshot_status", args)


def notebook_list_cells(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("notebook_list_cells", args)


def notebook_get_cell(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("notebook_get_cell", args)


def notebook_get_cells(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("notebook_get_cells", args)


def notebook_find_symbol(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("notebook_find_symbol", args)


def notebook_search(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("notebook_search", args)


def notebook_cell_neighbors(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("notebook_cell_neighbors", args)


def creating_markdown_by_index(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("creating_markdown_by_index", args)


def insert_and_edit_cell(args: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a new code cell below `index`, then paste `content` into it."""
    try:
        from .bot_command import run_insert_code_below_flow
    except Exception:
        from bot_command import run_insert_code_below_flow

    idx = args.get("index")
    if idx is None:
        idx = args.get("cell_index") or args.get("cellIndex")
    cmd = {
        "action": "insert_code_below",
        "url": args.get("url"),
        "index": idx,
        "cell_index": idx,
        "cellIndex": idx,
        "content": args.get("content", ""),
        "tabId": args.get("tab_id") or args.get("tabId"),
        "tab_id": args.get("tab_id") or args.get("tabId"),
    }
    event = run_insert_code_below_flow(cmd, timeout=float(args.get("timeout", 25)))
    if not event.get("ok"):
        return {"ok": False, "phase": "insert_code_below_failed", "details": event}
    inner = event.get("result") if isinstance(event.get("result"), dict) else {}
    return {"ok": True, **inner, "phase": inner.get("phase", "insert_code_below_complete")}


def convert_cell_to_markdown_by_index(args: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience adapter that invokes the creating_markdown_by_index tool.
    Expects args: index, tab_id (optional), url (optional)
    """
    return _registry().call("creating_markdown_by_index", args)
