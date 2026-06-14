"""Shared validation and normalization for browser notebook tools."""

from __future__ import annotations

from typing import Any

# Shared browser timing — background /edit tabs need longer budgets (agentic ReAct chain).
BROWSER_CLICK_TIMEOUT_SEC = 6.0
BROWSER_CLICK_MAX_WAIT_MS = 400
BROWSER_EDIT_TIMEOUT_SEC = 12.0
BROWSER_EDIT_MAX_WAIT_MS = 600
BROWSER_INSERT_TIMEOUT_SEC = 8.0
BROWSER_INSERT_MAX_WAIT_MS = 400
BROWSER_DELETE_TIMEOUT_SEC = 8.0
BROWSER_SELECT_TIMEOUT_SEC = 4.0
BROWSER_SELECT_MAX_WAIT_MS = 400
BROWSER_MARKDOWN_TIMEOUT_SEC = 14.0
BROWSER_RUN_TIMEOUT_SEC = 6.0
BROWSER_RUN_MAX_WAIT_MS = 240
BROWSER_EDIT_AND_RUN_TIMEOUT_SEC = 12.0
BROWSER_COMPOSITE_TIMEOUT_SEC = 14.0

try:
    from .cell_index import app_to_dom, dom_to_app
except Exception:
    from cell_index import app_to_dom, dom_to_app

# Chrome tab IDs are typically large integers; DOM cell indices are small.
_TAB_ID_LIKE_MIN = 50_000
_MAX_DOM_CELL_INDEX = 499


def pick_cell_index(args: dict) -> Any:
    for key in ("dom_index", "domIndex", "cell_index", "cellIndex", "index"):
        if key in args and args.get(key) is not None:
            return args.get(key)
    return None


def pick_tab_id(args: dict) -> int | None:
    for key in ("tab_id", "tabId"):
        raw = args.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
    return None


def pick_notebook_url(args: dict) -> str:
    for key in ("url", "tabUrl", "tab_url"):
        raw = args.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _looks_like_tab_id(value: int) -> bool:
    return value >= _TAB_ID_LIKE_MIN


def normalize_dom_cell_index(raw: Any, *, url: str | None = None) -> tuple[int | None, str | None]:
    """
    Return (0-based DOM index, error).

    Matches Kaggle/Jupyter `data-windowed-list-index` on cell elements (first cell = 0).
    """
    if raw is None:
        return None, "cell_index is required (0-based DOM index; first cell is 0, attribute data-windowed-list-index=\"0\")"

    try:
        idx = int(raw)
    except (TypeError, ValueError):
        return None, f"cell_index must be an integer, got {raw!r}"

    if idx < 0:
        return None, f"cell_index must be >= 0 (DOM index), got {idx}"

    if _looks_like_tab_id(idx):
        return None, (
            f"cell_index {idx} looks like a browser tab id, not a DOM cell index. "
            "Pass tab_id separately; use cell_index 0 for the first notebook cell."
        )

    if idx > _MAX_DOM_CELL_INDEX:
        return None, f"cell_index {idx} exceeds maximum {_MAX_DOM_CELL_INDEX}"

    if url:
        bounds_err = _validate_dom_against_snapshot(url, idx)
        if bounds_err:
            return None, bounds_err

    return idx, None


def normalize_dom_cell_index_from_args(
    args: dict,
    *,
    url: str | None = None,
    default_basis: str = "dom",
) -> tuple[int | None, str | None]:
    """
    Resolve DOM index from tool args.

    Accepts:
    - dom_index / cell_index as 0-based DOM index when index_basis=dom
    - cell_index as 1-based label when index_basis=app (default for click_cell)
    """
    basis = str(args.get("index_basis") or args.get("indexBasis") or default_basis).strip().lower()
    raw = pick_cell_index(args)
    if raw is None:
        if basis in {"app", "1", "1-based", "one_based"}:
            return None, "cell_index is required (1-based label; first cell is 1)"
        return None, "cell_index is required (0-based DOM index; first cell is 0)"

    if basis in {"app", "1", "1-based", "one_based"}:
        try:
            app_idx = int(raw)
        except (TypeError, ValueError):
            return None, f"cell_index must be an integer, got {raw!r}"
        if app_idx < 1:
            return None, f"cell_index must be >= 1 (first labeled cell is 1), got {app_idx}"
        if _looks_like_tab_id(app_idx):
            return None, f"cell_index {app_idx} looks like a tab id, not a notebook cell"
        if url:
            bounds_err = _validate_app_against_snapshot(url, app_idx)
            if bounds_err:
                return None, bounds_err
        return app_to_dom(app_idx), None

    return normalize_dom_cell_index(raw, url=url)


def _validate_app_against_snapshot(url: str, app_index: int) -> str | None:
    try:
        from .local_notebook_tools import notebook_list_cells
    except Exception:
        from local_notebook_tools import notebook_list_cells

    listing = notebook_list_cells({"url": url})
    if not listing.get("ok"):
        return None

    count = int(listing.get("cell_count") or 0)
    if count < 1:
        return None

    if app_index < 1 or app_index > count:
        return (
            f"cell_index {app_index} is out of range (notebook has {count} cells, "
            f"valid labels are 1..{count})."
        )
    return None


def _validate_dom_against_snapshot(url: str, dom_index: int) -> str | None:
    try:
        from .local_notebook_tools import notebook_list_cells
    except Exception:
        from local_notebook_tools import notebook_list_cells

    listing = notebook_list_cells({"url": url})
    if not listing.get("ok"):
        return None

    count = int(listing.get("cell_count") or 0)
    if count < 1:
        return None

    if dom_index >= count:
        return (
            f"cell_index {dom_index} is out of range for the DOM (notebook has {count} cells, "
            f"valid DOM indices are 0..{count - 1}). "
            f"Inspect data-windowed-list-index on the target cell."
        )
    return None


def is_retriable_browser_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = str(error).lower()
    needles = (
        "cell not found",
        "no frame accepted",
        "timeout",
        "no extension response",
        "no notebook surface",
        "not found in this frame",
        "could not resolve notebook tab",
        "scroll/wait exhausted",
    )
    return any(n in lowered for n in needles)


def normalize_click_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """
    Build a normalized bot command for click_cell.
    Returns (cmd, error_payload) where error_payload is {"ok": False, ...}.

    cellIndex sent to the extension is the 0-based DOM index (data-windowed-list-index).
    """
    url = pick_notebook_url(args)
    if not url:
        return None, {"ok": False, "error": "url is required (open notebook /edit URL)"}

    dom_index, err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if err:
        return None, {"ok": False, "error": err}

    app_index = dom_to_app(dom_index)

    cmd: dict[str, Any] = {
        "action": "click",
        "url": url,
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "scrollIntoView": args.get("scroll_into_view", args.get("scrollIntoView", True)) is not False,
        "runCell": False,
    }

    tab_id = pick_tab_id(args)
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id

    return cmd, None


def normalize_run_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for run_cell (1-based cell_index, executes cell in kernel)."""
    url = pick_notebook_url(args)
    if not url:
        return None, {"ok": False, "error": "url is required (open notebook /edit URL)"}

    dom_index, err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if err:
        return None, {"ok": False, "error": err}

    app_index = dom_to_app(dom_index)

    cmd: dict[str, Any] = {
        "action": "run_cell",
        "url": url,
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "scrollIntoView": args.get("scroll_into_view", args.get("scrollIntoView", True)) is not False,
        "runCell": True,
    }

    tab_id = pick_tab_id(args)
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id

    return cmd, None


def normalize_edit_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for edit_cell_by_index (1-based cell_index, replaces cell content)."""
    url = pick_notebook_url(args)
    if not url:
        return None, {"ok": False, "error": "url is required (open notebook /edit URL)"}

    dom_index, err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if err:
        return None, {"ok": False, "error": err}

    content = args.get("content")
    if content is None:
        content = args.get("input")
    if content is None:
        return None, {"ok": False, "error": "content is required (cell source text to paste)"}

    app_index = dom_to_app(dom_index)

    cmd: dict[str, Any] = {
        "action": "edit_cell_by_index",
        "url": url,
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "content": str(content),
    }

    tab_id = pick_tab_id(args)
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id

    return cmd, None


def normalize_edit_and_run_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for edit_and_run_cell (replace source, then execute)."""
    cmd, err = normalize_edit_cell_args(args)
    if err:
        return None, err
    cmd["action"] = "edit_and_run_cell"
    cmd["runCell"] = True
    return cmd, None


def normalize_insert_and_edit_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for insert_and_edit_cell (1-based anchor, insert below, then fill)."""
    url = pick_notebook_url(args)
    if not url:
        return None, {"ok": False, "error": "url is required (open notebook /edit URL)"}

    dom_index, err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if err:
        return None, {"ok": False, "error": err}

    content = args.get("content")
    if content is None:
        content = args.get("input")
    if content is None:
        return None, {"ok": False, "error": "content is required (source for the new cell)"}

    direction = str(args.get("direction") or "below").strip().lower()
    if direction not in {"below", "above"}:
        return None, {"ok": False, "error": "direction must be 'below' or 'above'"}

    app_index = dom_to_app(dom_index)

    cmd: dict[str, Any] = {
        "action": "insert_and_edit_cell",
        "url": url,
        "cellIndex": dom_index,
        "cell_index": app_index,
        "index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "direction": direction,
        "content": str(content),
    }

    tab_id = pick_tab_id(args)
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id

    return cmd, None


def normalize_select_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for select_cell_by_index (focus cell, no run)."""
    from .browser_tool_response import validation_error

    url = pick_notebook_url(args)
    if not url:
        return None, validation_error("select_cell_by_index", "url is required (open notebook /edit URL)")

    dom_index, err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if err:
        return None, validation_error("select_cell_by_index", err)

    app_index = dom_to_app(dom_index)
    cmd: dict[str, Any] = {
        "action": "select_cell_by_index",
        "url": url,
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "scrollIntoView": args.get("scroll_into_view", args.get("scrollIntoView", True)) is not False,
        "runCell": False,
    }
    tab_id = pick_tab_id(args)
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id
    return cmd, None


def normalize_insert_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for insert_cell (empty cell above/below anchor)."""
    from .browser_tool_response import validation_error

    url = pick_notebook_url(args)
    if not url:
        return None, validation_error("insert_cell", "url is required (open notebook /edit URL)")

    anchor_raw = args.get("index")
    if anchor_raw is None:
        anchor_raw = args.get("cell_index")
    if anchor_raw is None:
        anchor_raw = args.get("cellIndex")

    dom_index, err = normalize_dom_cell_index_from_args(
        {"cell_index": anchor_raw, "index_basis": args.get("index_basis", "app")},
        url=None,
        default_basis="app",
    )
    if err:
        return None, validation_error("insert_cell", err)

    direction = str(args.get("direction") or "below").strip().lower()
    if direction not in {"below", "above"}:
        return None, validation_error("insert_cell", "direction must be 'below' or 'above'")

    app_index = dom_to_app(dom_index)
    cmd: dict[str, Any] = {
        "action": "insert_cell",
        "url": url,
        "cellIndex": dom_index,
        "cell_index": app_index,
        "index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "direction": direction,
    }
    tab_id = pick_tab_id(args)
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id
    return cmd, None


def normalize_delete_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for delete_by_index (1-based cell_index)."""
    from .browser_tool_response import validation_error

    url = pick_notebook_url(args)
    if not url:
        return None, validation_error("delete_by_index", "url is required (open notebook /edit URL)")

    dom_index, err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if err:
        return None, validation_error("delete_by_index", err)

    app_index = dom_to_app(dom_index)
    cmd: dict[str, Any] = {
        "action": "delete_by_index",
        "url": url,
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "scrollIntoView": args.get("scroll_into_view", args.get("scrollIntoView", True)) is not False,
        "maxWaitMs": args.get("maxWaitMs", args.get("max_wait_ms", 600)),
    }
    tab_id = pick_tab_id(args)
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id
    return cmd, None


def normalize_creating_markdown_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for creating_markdown_by_index (insert markdown above anchor)."""
    from .browser_tool_response import validation_error

    url = pick_notebook_url(args)
    if not url:
        return None, validation_error("creating_markdown_by_index", "url is required (open notebook /edit URL)")

    anchor_raw = args.get("index")
    if anchor_raw is None:
        anchor_raw = args.get("cell_index")
    if anchor_raw is None:
        anchor_raw = args.get("cellIndex")

    dom_index, err = normalize_dom_cell_index_from_args(
        {"cell_index": anchor_raw, "index_basis": args.get("index_basis", "app")},
        url=None,
        default_basis="app",
    )
    if err:
        return None, validation_error("creating_markdown_by_index", err)

    app_index = dom_to_app(dom_index)
    cmd: dict[str, Any] = {
        "action": "creating_markdown_by_index",
        "url": url,
        "cellIndex": dom_index,
        "cell_index": app_index,
        "index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
    }
    tab_id = pick_tab_id(args)
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id
    return cmd, None


# Back-compat alias used by tests/docs migrating from 1-based naming.
def normalize_cell_index(raw: Any, *, url: str | None = None) -> tuple[int | None, str | None]:
    return normalize_dom_cell_index(raw, url=url)
