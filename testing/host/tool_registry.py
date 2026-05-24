import json
import uuid
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
        insert_pos = max(0, idx if direction == "above" else idx + 1)
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


def _browser_tool(action: str):
    def _runner(args: dict) -> dict:
        try:
            from .bot_command import execute_bot_command_sync
        except Exception:
            from bot_command import execute_bot_command_sync

        url = _pick("url", "tabUrl", "tab_url", src=args) or ""
        if not url:
            return {"ok": False, "error": "url is required (pass the open notebook URL)"}

        cmd = {
            "action": action,
            "requestId": str(uuid.uuid4()),
            "url": url,
        }
        tab_id = _pick("tab_id", "tabId", src=args)
        if isinstance(tab_id, int) and tab_id > 0:
            cmd["tabId"] = tab_id

        cell_index = _pick("cell_index", "cellIndex", "index", src=args)
        if cell_index is not None:
            cmd["cellIndex"] = cell_index

        if action == "insert_cell":
            cmd["direction"] = args.get("direction", "below")
        if action in {"edit_cell_by_index", "edit_cell"}:
            cmd["content"] = args.get("content") or args.get("input") or ""

        timeout = float(args.get("timeout", 12.0))
        event = execute_bot_command_sync(cmd, timeout=timeout)
        inner = event.get("result") if isinstance(event.get("result"), dict) else {}
        if event.get("ok"):
            return {"ok": True, **inner}
        return {"ok": False, "error": event.get("error") or inner.get("error") or "tool failed", "details": event}

    return _runner


def _register_default_tools():
    url_schema = {"type": "string"}
    cell_schema = {"type": "integer"}
    candidates = [
        (
            "click_cell",
            {
                "type": "object",
                "properties": {"cell_index": cell_schema, "url": url_schema},
                "required": ["cell_index", "url"],
            },
            "Click a notebook cell by index in the browser",
        ),
        (
            "select_cell_by_index",
            {
                "type": "object",
                "properties": {"cell_index": cell_schema, "url": url_schema},
                "required": ["cell_index", "url"],
            },
            "Select a notebook cell by index in the browser",
        ),
        (
            "insert_cell",
            {
                "type": "object",
                "properties": {"index": cell_schema, "direction": {"type": "string"}, "url": url_schema},
                "required": ["index", "url"],
            },
            "Insert a cell above/below a given index in the browser",
        ),
        (
            "edit_cell_by_index",
            {
                "type": "object",
                "properties": {"cell_index": cell_schema, "content": {"type": "string"}, "url": url_schema},
                "required": ["cell_index", "url"],
            },
            "Focus and edit a notebook cell by index",
        ),
        (
            "delete_by_index",
            {
                "type": "object",
                "properties": {"cell_index": cell_schema, "url": url_schema},
                "required": ["cell_index", "url"],
            },
            "Delete a notebook cell by index in the browser",
        ),
        (
            "creating_markdown_by_index",
            {
                "type": "object",
                "properties": {"index": cell_schema, "url": url_schema},
                "required": ["index", "url"],
            },
            "Insert a new cell above a given index and convert it to Markdown",
        ),
    ]

    action_map = {
        "click_cell": "click",
        "select_cell_by_index": "select_cell_by_index",
        "insert_cell": "insert_cell",
        "edit_cell_by_index": "edit_cell_by_index",
        "delete_by_index": "delete_by_index",
        "creating_markdown_by_index": "creating_markdown_by_index",
    }

    for name, schema, desc in candidates:
        action = action_map.get(name, name)
        _REGISTRY.register(name, schema, desc, _browser_tool(action))


_register_default_tools()


def _notebook_graph_wrapper(args: dict) -> dict:
    try:
        from .persistence_helpers import read_json_file, get_safe_filename
        from .config import SCRAPED_DIR
    except Exception:
        try:
            from persistence_helpers import read_json_file, get_safe_filename
            from testing.host.config import SCRAPED_DIR
        except Exception:
            return {"type": "GRAPH_DATA", "graph": [], "error": "persistence helpers unavailable"}

    url = args.get("url") or args.get("tabUrl") or args.get("tab_url") or ""
    if not url:
        return {"type": "GRAPH_DATA", "graph": [], "error": "No url provided"}

    filename = get_safe_filename(url)
    persistent_path = SCRAPED_DIR / "persistent" / filename
    if not persistent_path.exists():
        return {"type": "GRAPH_DATA", "graph": [], "error": "No persistent notebook snapshot found", "url": url}

    data = read_json_file(persistent_path)
    if not data or not isinstance(data.get("cells"), list):
        return {"type": "GRAPH_DATA", "graph": [], "error": "No notebook data", "url": url}

    graph = []
    for cell in data.get("cells", []):
        try:
            idx = int(cell.get("index", 0))
        except Exception:
            continue
        preview = str(cell.get("input") or "")[:120]
        graph.append({"cell_number": idx, "input_preview": preview, "dependencies": []})

    return {"type": "GRAPH_DATA", "graph": graph, "error": None, "url": url}


try:
    _REGISTRY.register(
        "notebook_graph_query",
        {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        "Query the persistent notebook snapshot and return a small graph summary",
        _notebook_graph_wrapper,
    )
except Exception:
    pass


def build_cerebras_tools():
    tools = []
    for name, entry in _REGISTRY._tools.items():
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

        examples_path = prompt_dir / "tool_examples_autogen.txt"
        ex_lines = []
        curated = {
            "click_cell": {"cell_index": 3, "url": "https://www.kaggle.com/code/alice/sample-notebook"},
            "select_cell_by_index": {"cell_index": 2, "url": "https://www.kaggle.com/code/alice/sample-notebook"},
            "insert_cell": {"index": 2, "direction": "below", "url": "https://www.kaggle.com/code/alice/sample-notebook"},
            "edit_cell_by_index": {"cell_index": 4, "content": "# updated code\nprint(\"hello from agent\")", "url": "https://www.kaggle.com/code/alice/sample-notebook"},
            "delete_by_index": {"cell_index": 5, "url": "https://www.kaggle.com/code/alice/sample-notebook"},
            "notebook_graph_query": {"url": "https://www.kaggle.com/code/codekey/qwen2_5_coder_7b_instruct_edit"},
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
