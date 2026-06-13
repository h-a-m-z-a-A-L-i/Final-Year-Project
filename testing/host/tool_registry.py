import json
from pathlib import Path
from typing import Callable, Dict, Any

try:
    from jsonschema import validate as _js_validate, ValidationError as _ValidationError
except Exception:
    _js_validate = None
    _ValidationError = Exception

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, schema: dict, description: str, func: Callable[[dict], dict]):
        self._tools[name] = {
            "schema": schema,
            "description": description,
            "func": func,
        }

    def get(self, name: str):
        return self._tools.get(name)

    def call(self, name: str, args: dict, timeout: float = 12.0) -> dict:
        entry = self.get(name)
        if not entry:
            raise KeyError(f"Tool not found: {name}")
        func = entry.get("func")
        if not callable(func):
            raise TypeError("Registered tool is not callable")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise TypeError("Tool args must be an object/dict")
        schema = entry.get("schema")
        if _js_validate and isinstance(schema, dict):
            try:
                _js_validate(instance=args, schema=schema)
            except _ValidationError as ve:
                return {"ok": False, "error": f"Invalid arguments: {ve.message if hasattr(ve, 'message') else str(ve)}"}
        return func(args)


_REGISTRY = ToolRegistry()


def registry() -> ToolRegistry:
    return _REGISTRY


def _pick(*names, src: dict):
    for n in names:
        if n in src and src.get(n) is not None:
            return src.get(n)
    return None


def sync_persistence_for_action(action: str, cmd: dict, browser_result: dict) -> None:
    """Update persistent notebook JSON after a successful browser operation."""
    try:
        from .persistence_helpers import read_json_file, save_persistent_json, get_safe_filename
        from .config import SCRAPED_DIR
    except Exception:
        try:
            from persistence_helpers import read_json_file, save_persistent_json, get_safe_filename
            from testing.host.config import SCRAPED_DIR
        except Exception:
            return

    url = _pick("url", "tabUrl", "tab_url", src=cmd) or ""
    if not url:
        return

    action = str(action or "").strip().lower()
    filename = get_safe_filename(url)
    ppath = SCRAPED_DIR / "persistent" / filename

    if action == "insert_cell":
        direction = cmd.get("direction", "below")
        try:
            idx_raw = _pick("index", "cell_index", "cellIndex", src=cmd)
            idx = int(idx_raw) if idx_raw is not None else 0
        except Exception:
            idx = 0
        data = read_json_file(ppath) or {"cells": []}
        cells = list(data.get("cells") or [])
        insert_pos = max(0, (idx - 1) if direction == "above" else idx)
        new_cell = {"type": "code", "index": 0, "input": "", "output": "", "execution_order": None, "execution_title": ""}
        if insert_pos >= len(cells):
            cells.append(new_cell)
        else:
            cells.insert(insert_pos, new_cell)
        for i, c in enumerate(cells, start=1):
            c["index"] = int(i)
        data["cells"] = cells
        save_persistent_json(data, url)
        return

    if action in {"edit_cell_by_index", "edit_cell"}:
        try:
            idx = int(_pick("cell_index", "cellIndex", "index", src=cmd))
        except Exception:
            return
        content = cmd.get("content") or cmd.get("input") or ""
        data = read_json_file(ppath) or {"cells": []}
        cells = list(data.get("cells") or [])
        for c in cells:
            if int(c.get("index", 0)) == idx:
                c["input"] = content
                data["cells"] = cells
                save_persistent_json(data, url)
                return
        return

    if action == "delete_by_index":
        try:
            idx = int(_pick("cell_index", "cellIndex", "index", src=cmd))
        except Exception:
            return
        data = read_json_file(ppath) or {"cells": []}
        cells = [c for c in data.get("cells") or [] if int(c.get("index", 0)) != idx]
        for i, c in enumerate(cells, start=1):
            c["index"] = int(i)
        data["cells"] = cells
        save_persistent_json(data, url)


def _load_browser_tool_runner(mod_name: str, func_name: str):
    import importlib

    try:
        mod = importlib.import_module(f".{mod_name}", package=__package__ or "testing.host")
    except Exception:
        mod = importlib.import_module(mod_name)
    return getattr(mod, func_name)


def _register_default_tools():
    url_schema = {"type": "string"}
    cell_schema = {"type": "integer"}
    candidates = [
        (
            "click_cell",
            {
                "type": "object",
                "properties": {
                    "cell_index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based cell label (first cell is 1; matches notebook JSON index)",
                    },
                    "dom_index": {"type": "integer", "minimum": 0},
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                    "index_basis": {"type": "string", "enum": ["dom", "app"]},
                    "run_cell": {"type": "boolean"},
                    "scroll_into_view": {"type": "boolean"},
                },
                "required": ["url"],
            },
            "Focus or run a notebook cell by 1-based cell label (first cell is 1; converted to DOM index internally)",
        ),
        (
            "select_cell_by_index",
            {
                "type": "object",
                "properties": {
                    "cell_index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based cell label (first cell is 1)",
                    },
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                },
                "required": ["cell_index", "url"],
            },
            "Select/focus a notebook cell by 1-based label without running it",
        ),
        (
            "insert_cell",
            {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based anchor cell label",
                    },
                    "direction": {"type": "string", "enum": ["below", "above"]},
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                },
                "required": ["index", "url"],
            },
            "Insert an empty code cell above/below a 1-based anchor cell",
        ),
        (
            "edit_cell_by_index",
            {
                "type": "object",
                "properties": {
                    "cell_index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based cell label (first cell is 1)",
                    },
                    "content": {"type": "string"},
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                },
                "required": ["cell_index", "url", "content"],
            },
            "Replace a notebook cell's source by 1-based cell label (select + paste content)",
        ),
        (
            "insert_and_edit_cell",
            {
                "type": "object",
                "properties": {
                    "cell_index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based anchor cell (new cell inserted below this label)",
                    },
                    "content": {"type": "string"},
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["below", "above"]},
                },
                "required": ["cell_index", "url", "content"],
            },
            "Insert a new code cell below a 1-based anchor cell and paste content into it",
        ),
        (
            "run_cell",
            {
                "type": "object",
                "properties": {
                    "cell_index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based cell label to execute (first cell is 1)",
                    },
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                    "scroll_into_view": {"type": "boolean"},
                },
                "required": ["cell_index", "url"],
            },
            "Execute a notebook code cell by 1-based cell label (select cell and run in kernel)",
        ),
        (
            "edit_and_run_cell",
            {
                "type": "object",
                "properties": {
                    "cell_index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based cell label to edit and execute",
                    },
                    "content": {"type": "string"},
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                },
                "required": ["cell_index", "url", "content"],
            },
            "Replace a cell's source by 1-based label, then run it in the kernel (edit + execute)",
        ),
        (
            "delete_by_index",
            {
                "type": "object",
                "properties": {
                    "cell_index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based cell label to delete",
                    },
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                },
                "required": ["cell_index", "url"],
            },
            "Delete a notebook cell by 1-based cell label",
        ),
        (
            "creating_markdown_by_index",
            {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based anchor cell (markdown inserted above)",
                    },
                    "url": url_schema,
                    "tab_id": {"type": "integer"},
                },
                "required": ["index", "url"],
            },
            "Insert a new markdown cell above a 1-based anchor cell",
        ),
    ]

    _TOOL_RUNNERS = {
        "click_cell": ("click_cell_tool", "run_click_cell"),
        "select_cell_by_index": ("select_cell_tool", "run_select_cell"),
        "insert_cell": ("insert_cell_tool", "run_insert_cell"),
        "edit_cell_by_index": ("edit_cell_tool", "run_edit_cell"),
        "insert_and_edit_cell": ("insert_and_edit_cell_tool", "run_insert_and_edit_cell"),
        "run_cell": ("run_cell_tool", "run_run_cell"),
        "edit_and_run_cell": ("edit_and_run_cell_tool", "run_edit_and_run_cell"),
        "delete_by_index": ("delete_cell_tool", "run_delete_cell"),
        "creating_markdown_by_index": ("creating_markdown_tool", "run_creating_markdown"),
    }

    for name, schema, desc in candidates:
        mod_name, func_name = _TOOL_RUNNERS[name]
        _REGISTRY.register(name, schema, desc, _load_browser_tool_runner(mod_name, func_name))


_register_default_tools()


def _register_local_notebook_tools():
    try:
        from .local_notebook_tools import register_local_tools
    except Exception:
        from local_notebook_tools import register_local_tools
    register_local_tools(_REGISTRY)


_register_local_notebook_tools()


def build_local_tool_descriptions() -> str:
    lines = []
    try:
        from .local_notebook_tools import LOCAL_TOOL_NAMES
    except Exception:
        from local_notebook_tools import LOCAL_TOOL_NAMES
    for name in sorted(LOCAL_TOOL_NAMES):
        entry = _REGISTRY.get(name)
        if not entry:
            continue
        schema = entry.get("schema") or {}
        desc = entry.get("description") or ""
        lines.append(f"{name}: {desc} Args schema: {json.dumps(schema)}")
    return "\n".join(lines)


def build_cerebras_tools(*, local_only: bool = True):
    """Expose tools to the LLM. Default: local JSON read tools only (no browser)."""
    try:
        from .local_notebook_tools import LOCAL_TOOL_NAMES
    except Exception:
        from local_notebook_tools import LOCAL_TOOL_NAMES

    tools = []
    for name, entry in _REGISTRY._tools.items():
        if local_only and name not in LOCAL_TOOL_NAMES:
            continue
        schema = entry.get("schema")
        if not isinstance(schema, dict):
            continue
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": entry.get("description") or name,
                "parameters": schema,
            },
        })
    return tools


def generate_prompt_autogen():
    try:
        prompt_dir = ROOT / "prompts" / "tool_calling"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        out_path = prompt_dir / "tool_descriptions_autogen.txt"
        lines = []
        for k, v in _REGISTRY._tools.items():
            schema = v.get("schema") or {}
            desc = v.get("description") or ""
            lines.append(f"{k}: {desc} Args schema: {json.dumps(schema)}")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        local_path = prompt_dir / "local_tool_descriptions_autogen.txt"
        local_path.write_text(build_local_tool_descriptions(), encoding="utf-8")

        examples_path = prompt_dir / "tool_examples_autogen.txt"
        ex_lines = []
        curated = {
            "notebook_snapshot_status": {"url": "https://www.kaggle.com/code/alice/sample-notebook"},
            "notebook_list_cells": {"url": "https://www.kaggle.com/code/alice/sample-notebook"},
            "notebook_graph_query": {"url": "https://www.kaggle.com/code/alice/sample-notebook"},
            "notebook_get_cell": {"url": "https://www.kaggle.com/code/alice/sample-notebook", "cell_index": 3},
            "notebook_find_symbol": {"url": "https://www.kaggle.com/code/alice/sample-notebook", "symbol": "model_df"},
            "notebook_search": {"url": "https://www.kaggle.com/code/alice/sample-notebook", "query": "read_csv"},
            "notebook_cell_neighbors": {"url": "https://www.kaggle.com/code/alice/sample-notebook", "cell_index": 4},
            "notebook_recommend_placement": {
                "url": "https://www.kaggle.com/code/alice/sample-notebook",
                "symbols": ["model_df"],
            },
        }

        for k, v in _REGISTRY._tools.items():
            if k in curated:
                example = curated[k]
            else:
                schema = v.get("schema") or {}
                props = schema.get("properties") or {}
                example = {}
                for pname, prop in props.items():
                    ptype = prop.get("type") if isinstance(prop, dict) else None
                    if ptype == "integer":
                        example[pname] = 1
                    elif ptype == "number":
                        example[pname] = 1.0
                    elif ptype == "boolean":
                        example[pname] = True
                    else:
                        example[pname] = "https://www.kaggle.com/code/alice/sample-notebook"
            ex_lines.append(f"{k} example args: {json.dumps(example, ensure_ascii=False)}")
        examples_path.write_text("\n".join(ex_lines), encoding="utf-8")
    except Exception:
        pass


generate_prompt_autogen()


if __name__ == "__main__":
    print("Tool registry contains:", list(_REGISTRY._tools.keys()))
