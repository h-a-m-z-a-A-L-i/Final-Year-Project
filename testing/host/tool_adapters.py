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


def creating_markdown_by_index(args: Dict[str, Any]) -> Dict[str, Any]:
    return _registry().call("creating_markdown_by_index", args)


def insert_and_edit_cell(args: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a cell and then edit it. Expects args: url, index, direction, content"""
    # First insert
    insert_args = {k: args.get(k) for k in ("url", "index", "direction") if args.get(k) is not None}
    insert_res = _registry().call("insert_cell", insert_args)
    if not insert_res.get("ok"):
        return {"ok": False, "phase": "insert_failed", "details": insert_res}

    # Determine new cell index from result if provided, else assume index+1 for 'below'
    new_idx = insert_res.get("cellIndex") or (int(args.get("index", 0)) + (1 if args.get("direction", "below") == "below" else 0))

    edit_args = {"url": args.get("url"), "cell_index": new_idx, "content": args.get("content", "")}
    edit_res = _registry().call("edit_cell_by_index", edit_args)
    if not edit_res.get("ok"):
        return {"ok": False, "phase": "edit_failed", "insert_result": insert_res, "edit_result": edit_res}

    return {"ok": True, "phase": "insert_and_edit_complete", "insert": insert_res, "edit": edit_res}


def convert_cell_to_markdown_by_index(args: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience adapter that invokes the creating_markdown_by_index tool.
    Expects args: index, tab_id (optional), url (optional)
    """
    return _registry().call("creating_markdown_by_index", args)
