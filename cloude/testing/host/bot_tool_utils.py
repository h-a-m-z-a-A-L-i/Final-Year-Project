"""Shared validation and normalization for browser notebook tools."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# Shared browser timing — background /edit tabs need longer budgets (agentic ReAct chain).
BROWSER_CLICK_TIMEOUT_SEC = 6.0
BROWSER_CLICK_MAX_WAIT_MS = 400
BROWSER_EDIT_TIMEOUT_SEC = 12.0
BROWSER_EDIT_MAX_WAIT_MS = 600
BROWSER_INSERT_TIMEOUT_SEC = 22.0
BROWSER_INSERT_MAX_WAIT_MS = 2000
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


def apply_command_transport_flags(cmd: dict[str, Any], args: dict) -> dict[str, Any]:
    """Copy fire-and-forget / wait flags from tool args onto bot command dicts."""
    if args.get("fire_and_forget") is True or args.get("fireAndForget") is True:
        cmd["fire_and_forget"] = True
        cmd["wait_for_result"] = False
    elif args.get("wait_for_result") is False or args.get("waitForResult") is False:
        cmd["fire_and_forget"] = True
        cmd["wait_for_result"] = False
    return cmd
_MAX_DOM_CELL_INDEX = 499


def pick_cell_index(args: dict) -> Any:
    for key in ("dom_index", "domIndex", "cell_index", "cellIndex", "index"):
        if key in args and args.get(key) is not None:
            return args.get(key)
    return None


def pick_tab_id(args: dict) -> int | None:
    for key in ("tab_id", "tabId"):
        raw = args.get(key)
        if isinstance(raw, int) and _looks_like_tab_id(raw):
            return raw
    return None


def pick_notebook_url(args: dict) -> str:
    for key in ("url", "tabUrl", "tab_url"):
        raw = args.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _notebook_url_tool_names() -> frozenset[str]:
    try:
        from .tool_registry import BROWSER_TOOL_NAMES
        from .local_notebook_tools import LLM_LOCAL_TOOL_NAMES
    except Exception:
        from tool_registry import BROWSER_TOOL_NAMES
        from local_notebook_tools import LLM_LOCAL_TOOL_NAMES
    return frozenset(set(BROWSER_TOOL_NAMES) | set(LLM_LOCAL_TOOL_NAMES))


_NOTEBOOK_URL_TOOLS: frozenset[str] | None = None


def notebook_url_tool_names() -> frozenset[str]:
    global _NOTEBOOK_URL_TOOLS
    if _NOTEBOOK_URL_TOOLS is None:
        _NOTEBOOK_URL_TOOLS = _notebook_url_tool_names()
    return _NOTEBOOK_URL_TOOLS

_DATASET_PATH_MARKERS = ("/kaggle/input/", "kaggle/input/")
_FILE_SUFFIXES = (".csv", ".json", ".parquet", ".xlsx", ".xls", ".txt", ".py", ".ipynb", ".tsv")
_PROMPT_PLACEHOLDER_MARKERS = ("/code/owner/", "owner/slug", "{url}")


def _normalized_notebook_url(url: str) -> str:
    try:
        from .notebook_data_handler import _normalized_url
    except Exception:
        from notebook_data_handler import _normalized_url
    return _normalized_url(url or "")


def _notebook_url_variants(url: str) -> frozenset[str]:
    norm = _normalized_notebook_url(url)
    if not norm:
        return frozenset()
    variants: set[str] = {norm}
    try:
        parsed = urlparse(norm)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        if host == "www.kaggle.com":
            variants.add(f"{parsed.scheme}://kaggle.com{path}")
        elif host == "kaggle.com":
            variants.add(f"{parsed.scheme}://www.kaggle.com{path}")
    except Exception:
        pass
    return frozenset(variants)


def notebook_urls_match(url_a: str, url_b: str) -> bool:
    """True when two notebook URLs refer to the same /edit tab (incl. www vs bare host)."""
    a_variants = _notebook_url_variants(url_a)
    b_variants = _notebook_url_variants(url_b)
    if not a_variants or not b_variants:
        return False
    return bool(a_variants & b_variants)


def is_prompt_placeholder_notebook_url(url: str) -> bool:
    """True for prompt template URLs like owner/slug that must not reach the extension."""
    lower = str(url or "").strip().lower()
    if not lower:
        return False
    return any(marker in lower for marker in _PROMPT_PLACEHOLDER_MARKERS)


def is_valid_notebook_edit_url(url: str) -> bool:
    """True when url looks like an absolute notebook /edit URL, not a dataset or file path."""
    raw = str(url or "").strip()
    if not raw:
        return False
    lower = raw.lower()
    if lower.startswith("/") or lower.startswith("./") or lower.startswith("../"):
        return False
    if any(marker in lower for marker in _DATASET_PATH_MARKERS):
        return False
    if lower.endswith(_FILE_SUFFIXES):
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    path = (parsed.path or "").lower().rstrip("/")
    if is_prompt_placeholder_notebook_url(raw):
        return False
    if "kaggle.com/code" in lower:
        return True
    return path.endswith("/edit")


def _log_session_coercion(tool_name: str, message: str) -> None:
    label = tool_name or "tool"
    try:
        from .dispatcher import log
    except Exception:
        try:
            from dispatcher import log
        except Exception:
            import logging

            logging.getLogger(__name__).warning("[%s] %s", label, message)
            return
    log(f"[session_context] {label}: {message}")


def coerce_notebook_tool_url(
    args: dict,
    *,
    session_url: str,
    tool_name: str = "",
) -> dict:
    """Force session notebook URL when args carry a missing, invalid, or mismatched url."""
    if not isinstance(args, dict):
        return args
    if tool_name and tool_name not in notebook_url_tool_names():
        return args
    session = str(session_url or "").strip()
    if not session:
        return args
    out = dict(args)
    current = pick_notebook_url(out)
    if (
        not is_valid_notebook_edit_url(current)
        or is_prompt_placeholder_notebook_url(current)
        or not notebook_urls_match(current, session)
    ):
        if current and current != session:
            _log_session_coercion(
                tool_name,
                f"url {current!r} overridden with session url {session!r}",
            )
        out["url"] = session
    return out


def coerce_notebook_tool_session(
    args: dict,
    *,
    session_url: str,
    session_tab_id: int | None = None,
    tool_name: str = "",
) -> dict:
    """Force session notebook URL and tab_id from the originating chat request."""
    if not isinstance(args, dict):
        return args
    if tool_name and tool_name not in notebook_url_tool_names():
        return args
    out = coerce_notebook_tool_url(args, session_url=session_url, tool_name=tool_name)
    if not isinstance(session_tab_id, int) or session_tab_id <= 0:
        return out
    current_tab = pick_tab_id(out)
    if current_tab is None:
        out["tab_id"] = session_tab_id
        out["tabId"] = session_tab_id
    elif current_tab != session_tab_id:
        _log_session_coercion(
            tool_name,
            f"tab_id {current_tab} overridden with session tab_id {session_tab_id}",
        )
        out["tab_id"] = session_tab_id
        out["tabId"] = session_tab_id
    else:
        out["tab_id"] = session_tab_id
        out["tabId"] = session_tab_id
    return out


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


def resolve_browser_target(args: dict, tool_name: str) -> tuple[str, int | None, dict | None]:
    """Browser tools need url OR tab_id — extension resolves whichever is missing."""
    try:
        from .browser_tool_response import validation_error
    except Exception:
        from browser_tool_response import validation_error

    url = pick_notebook_url(args)
    tab_id = pick_tab_id(args)
    if not url and tab_id is None:
        return "", None, validation_error(
            tool_name,
            "url or tab is required (one is enough — matching /edit page or Chrome tab id)",
        )
    return url, tab_id, None


def apply_browser_target(cmd: dict, url: str, tab_id: int | None) -> dict:
    if url:
        cmd["url"] = url
    if tab_id is not None:
        cmd["tabId"] = tab_id
        cmd["tab_id"] = tab_id
    return cmd


def normalize_click_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """
    Build a normalized bot command for click_cell.
    Returns (cmd, error_payload) where error_payload is {"ok": False, ...}.

    cellIndex sent to the extension is the 0-based DOM index (data-windowed-list-index).
    """
    url, tab_id, err = resolve_browser_target(args, "click_cell")
    if err:
        return None, err

    dom_index, cell_err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if cell_err:
        return None, {"ok": False, "error": cell_err}

    app_index = dom_to_app(dom_index)

    cmd: dict[str, Any] = {
        "action": "click",
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "scrollIntoView": args.get("scroll_into_view", args.get("scrollIntoView", True)) is not False,
        "runCell": False,
    }
    apply_browser_target(cmd, url, tab_id)
    return cmd, None


def normalize_run_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for run_cell (1-based cell_index, executes cell in kernel)."""
    url, tab_id, err = resolve_browser_target(args, "run_cell")
    if err:
        return None, err

    dom_index, cell_err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if cell_err:
        return None, {"ok": False, "error": cell_err}

    app_index = dom_to_app(dom_index)

    cmd: dict[str, Any] = {
        "action": "run_cell",
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "scrollIntoView": args.get("scroll_into_view", args.get("scrollIntoView", True)) is not False,
        "runCell": True,
    }
    apply_browser_target(cmd, url, tab_id)
    return apply_command_transport_flags(cmd, args), None


def normalize_edit_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for edit_cell_by_index (1-based cell_index, replaces cell content)."""
    url, tab_id, err = resolve_browser_target(args, "edit_cell_by_index")
    if err:
        return None, err

    dom_index, cell_err = normalize_dom_cell_index_from_args(args, url=url or None, default_basis="app")
    if cell_err:
        return None, {"ok": False, "error": cell_err}

    content = args.get("content")
    if content is None:
        content = args.get("input")
    if content is None:
        return None, {"ok": False, "error": "content is required (cell source text to paste)"}

    app_index = dom_to_app(dom_index)

    cmd: dict[str, Any] = {
        "action": "edit_cell_by_index",
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "content": str(content),
    }
    apply_browser_target(cmd, url, tab_id)
    return apply_command_transport_flags(cmd, args), None


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
    url, tab_id, err = resolve_browser_target(args, "insert_and_edit_cell")
    if err:
        return None, err

    dom_index, cell_err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if cell_err:
        return None, {"ok": False, "error": cell_err}

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
        "cellIndex": dom_index,
        "cell_index": app_index,
        "index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "direction": direction,
        "content": str(content),
    }
    apply_browser_target(cmd, url, tab_id)
    return cmd, None


def normalize_select_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for select_cell_by_index (focus cell, no run)."""
    url, tab_id, err = resolve_browser_target(args, "select_cell_by_index")
    if err:
        return None, err

    dom_index, cell_err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if cell_err:
        from .browser_tool_response import validation_error
        return None, validation_error("select_cell_by_index", cell_err)

    app_index = dom_to_app(dom_index)
    cmd: dict[str, Any] = {
        "action": "select_cell_by_index",
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "scrollIntoView": args.get("scroll_into_view", args.get("scrollIntoView", True)) is not False,
        "runCell": False,
    }
    apply_browser_target(cmd, url, tab_id)
    return cmd, None


def normalize_insert_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for insert_cell (empty cell above/below anchor)."""
    url, tab_id, err = resolve_browser_target(args, "insert_cell")
    if err:
        return None, err

    anchor_raw = args.get("index")
    if anchor_raw is None:
        anchor_raw = args.get("cell_index")
    if anchor_raw is None:
        anchor_raw = args.get("cellIndex")

    dom_index, cell_err = normalize_dom_cell_index_from_args(
        {"cell_index": anchor_raw, "index_basis": args.get("index_basis", "app")},
        url=None,
        default_basis="app",
    )
    if cell_err:
        from .browser_tool_response import validation_error
        return None, validation_error("insert_cell", cell_err)

    direction = str(args.get("direction") or "below").strip().lower()
    if direction not in {"below", "above"}:
        from .browser_tool_response import validation_error
        return None, validation_error("insert_cell", "direction must be 'below' or 'above'")

    app_index = dom_to_app(dom_index)
    cmd: dict[str, Any] = {
        "action": "insert_cell",
        "cellIndex": dom_index,
        "cell_index": app_index,
        "index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "direction": direction,
    }
    apply_browser_target(cmd, url, tab_id)
    return apply_command_transport_flags(cmd, args), None


def normalize_delete_cell_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for delete_by_index (1-based cell_index)."""
    url, tab_id, err = resolve_browser_target(args, "delete_by_index")
    if err:
        return None, err

    dom_index, cell_err = normalize_dom_cell_index_from_args(args, url=None, default_basis="app")
    if cell_err:
        from .browser_tool_response import validation_error
        return None, validation_error("delete_by_index", cell_err)

    app_index = dom_to_app(dom_index)
    cmd: dict[str, Any] = {
        "action": "delete_by_index",
        "cellIndex": dom_index,
        "cell_index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
        "scrollIntoView": args.get("scroll_into_view", args.get("scrollIntoView", True)) is not False,
        "maxWaitMs": args.get("maxWaitMs", args.get("max_wait_ms", 600)),
    }
    apply_browser_target(cmd, url, tab_id)
    return apply_command_transport_flags(cmd, args), None


def normalize_creating_markdown_args(args: dict) -> tuple[dict | None, dict | None]:
    """Build bot command for creating_markdown_by_index (insert markdown above anchor)."""
    url, tab_id, err = resolve_browser_target(args, "creating_markdown_by_index")
    if err:
        return None, err

    anchor_raw = args.get("index")
    if anchor_raw is None:
        anchor_raw = args.get("cell_index")
    if anchor_raw is None:
        anchor_raw = args.get("cellIndex")

    dom_index, cell_err = normalize_dom_cell_index_from_args(
        {"cell_index": anchor_raw, "index_basis": args.get("index_basis", "app")},
        url=None,
        default_basis="app",
    )
    if cell_err:
        from .browser_tool_response import validation_error
        return None, validation_error("creating_markdown_by_index", cell_err)

    app_index = dom_to_app(dom_index)
    cmd: dict[str, Any] = {
        "action": "creating_markdown_by_index",
        "cellIndex": dom_index,
        "cell_index": app_index,
        "index": app_index,
        "dom_index": dom_index,
        "app_index": app_index,
        "index_basis": "dom",
    }
    apply_browser_target(cmd, url, tab_id)
    return apply_command_transport_flags(cmd, args), None


# Back-compat alias used by tests/docs migrating from 1-based naming.
def normalize_cell_index(raw: Any, *, url: str | None = None) -> tuple[int | None, str | None]:
    return normalize_dom_cell_index(raw, url=url)
