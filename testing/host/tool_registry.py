import json
import uuid
import importlib.util
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

    def call(self, name: str, args: dict, timeout: float = 8.0) -> dict:
        entry = self.get(name)
        if not entry:
            raise KeyError(f"Tool not found: {name}")
        func = entry.get("func")
        if not callable(func):
            raise TypeError("Registered tool is not callable")
        # Basic validation: ensure args is a dict
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise TypeError("Tool args must be an object/dict")
        # If jsonschema is available, validate against provided schema
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


def _load_tool_module(name: str):
    path = TOOLS_DIR / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"Tool module not found: {path}")
    spec = importlib.util.spec_from_file_location(f"testing.host.tools.{name}", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_simple_jsonl_wrapper(tool_name: str):
    """Create a wrapper callable that queues the command via the tool module
    and waits for a result using that module's helpers. This preserves existing
    JSONL-based integration used by the browser extension.
    """

    mod = _load_tool_module(tool_name)

    def _wrapper(args: dict) -> dict:
        # Each tool in tools/ exposes helpers: _make_request_id, _build_*_command, _queue_command, _wait_for_request_result
        request_id = None
        if hasattr(mod, "_make_request_id"):
            request_id = mod._make_request_id()
        else:
            request_id = str(uuid.uuid4())

        # Determine builder function name heuristically
        builder = None
        for attr in dir(mod):
            if attr.startswith("_build_"):
                builder = getattr(mod, attr)
                break

        if builder is None:
            # Fallback: expect caller provided full command as 'cmd'
            cmd = args.get("cmd")
            if not isinstance(cmd, dict):
                raise ValueError("Tool module has no builder and no 'cmd' provided")
        else:
            # Map common arg names
            tab_id = args.get("tab_id") or args.get("tabId")
            url = args.get("url") or args.get("url_path") or ""
            # try common fields
            if "cell" in builder.__name__ or "click" in builder.__name__:
                cell_index = args.get("cell_index") or args.get("cellIndex") or args.get("index")
                cmd = builder(request_id, tab_id, cell_index, url)
            elif "insert" in builder.__name__:
                direction = args.get("direction", "below")
                cmd = builder(request_id, tab_id, direction, url)
            else:
                # best-effort attempt
                try:
                    cmd = builder(request_id, tab_id, args.get("value"), url)
                except Exception:
                    cmd = {"requestId": request_id}

        # Queue command
        if hasattr(mod, "_queue_command"):
            mod._queue_command(cmd)
        else:
            raise RuntimeError("Tool module missing _queue_command helper")

        # Wait for result
        if hasattr(mod, "_wait_for_request_result"):
            timeout = float(args.get("timeout", 8.0))
            result = mod._wait_for_request_result(request_id, timeout)
            return result or {"ok": False, "error": "timeout or no result"}
        else:
            return {"ok": True, "note": "queued"}

    return _wrapper


# Register a few common tools with a minimal schema and description
def _register_default_tools():
    candidates = [
        ("click_cell", {"type": "object", "properties": {"cell_index": {"type": "integer"}}}, "Click a notebook cell by index"),
        ("insert_cell", {"type": "object", "properties": {"index": {"type": "integer"}}}, "Insert a cell above/below a given index"),
        ("edit_cell_by_index", {"type": "object", "properties": {"cell_index": {"type": "integer"}}}, "Edit a notebook cell by index"),
        ("delete_by_index", {"type": "object", "properties": {"cell_index": {"type": "integer"}}}, "Delete a notebook cell by index"),
        ("select_cell_by_index", {"type": "object", "properties": {"cell_index": {"type": "integer"}}}, "Select a notebook cell by index"),
        ("creating_markdown_by_index", {"type": "object", "properties": {"index": {"type": "integer"}}}, "Insert a new cell above a given index and convert it to Markdown"),
    ]
    # In-process implementations will import persistence helpers and config at call-time
    # so tests or runtime can patch `config.SCRAPED_DIR` without needing to reload modules.

    def _select_inproc(args: dict) -> dict:
        idx = args.get("cell_index") or args.get("cellIndex") or args.get("index")
        url = args.get("url") or args.get("tabUrl") or args.get("tab_url") or ""
        if idx is None:
            return {"ok": False, "error": "cell_index is required"}
        try:
            return {"ok": True, "phase": "cell_selected", "cellIndex": int(idx), "tabId": args.get("tab_id")}
        except Exception:
            return {"ok": False, "error": "invalid cell_index"}

    def _click_inproc(args: dict) -> dict:
        return _select_inproc(args)

    def _insert_inproc(args: dict) -> dict:
        # Import persistence helpers and config at call-time so callers can patch config.SCRAPED_DIR
        try:
            from .persistence_helpers import read_json_file, save_persistent_json, get_safe_filename
            from .config import SCRAPED_DIR
        except Exception:
            try:
                from persistence_helpers import read_json_file, save_persistent_json, get_safe_filename
                from testing.host.config import SCRAPED_DIR
            except Exception:
                return {"ok": False, "error": "persistence helpers unavailable"}

        url = args.get("url") or args.get("tabUrl") or args.get("tab_url") or ""
        if not url:
            return {"ok": False, "error": "url required"}
        direction = args.get("direction", "below")
        try:
            idx = int(args.get("index") or args.get("cell_index") or args.get("cellIndex") or 0)
        except Exception:
            idx = 0
        filename = get_safe_filename(url)
        ppath = SCRAPED_DIR / "persistent" / filename
        data = read_json_file(ppath) or {"cells": []}
        cells = list(data.get("cells") or [])
        # Convert to list insertion index (0-based)
        insert_pos = max(0, idx if direction == "above" else idx + 1)
        new_cell = {"type": "code", "index": 0, "input": "", "output": "", "execution_order": None, "execution_title": ""}
        if insert_pos >= len(cells):
            cells.append(new_cell)
            inserted_idx = len(cells)
        else:
            cells.insert(insert_pos, new_cell)
            inserted_idx = insert_pos + 1
        # renumber indices (1-based)
        for i, c in enumerate(cells, start=1):
            try:
                c["index"] = int(i)
            except Exception:
                c["index"] = i
        data["cells"] = cells
        try:
            save_persistent_json(data, url)
        except Exception as e:
            return {"ok": False, "error": f"failed to save notebook: {e}"}
        return {"ok": True, "phase": "inserted", "direction": direction, "cellIndex": inserted_idx}

    def _edit_inproc(args: dict) -> dict:
        try:
            from .persistence_helpers import read_json_file, save_persistent_json, get_safe_filename
            from .config import SCRAPED_DIR
        except Exception:
            try:
                from persistence_helpers import read_json_file, save_persistent_json, get_safe_filename
                from testing.host.config import SCRAPED_DIR
            except Exception:
                return {"ok": False, "error": "persistence helpers unavailable"}

        url = args.get("url") or args.get("tabUrl") or args.get("tab_url") or ""
        if not url:
            return {"ok": False, "error": "url required"}
        try:
            idx = int(args.get("cell_index") or args.get("cellIndex") or args.get("index"))
        except Exception:
            return {"ok": False, "error": "invalid index"}
        content = args.get("content") or args.get("input") or ""
        filename = get_safe_filename(url)
        ppath = SCRAPED_DIR / "persistent" / filename
        data = read_json_file(ppath) or {"cells": []}
        cells = list(data.get("cells") or [])
        for c in cells:
            try:
                if int(c.get("index", 0)) == int(idx):
                    c["input"] = content
                    data["cells"] = cells
                    save_persistent_json(data, url)
                    return {"ok": True, "phase": "edited", "cellIndex": idx}
            except Exception:
                continue
        return {"ok": False, "error": "cell not found"}

    def _delete_inproc(args: dict) -> dict:
        try:
            from .persistence_helpers import read_json_file, save_persistent_json, get_safe_filename
            from .config import SCRAPED_DIR
        except Exception:
            try:
                from persistence_helpers import read_json_file, save_persistent_json, get_safe_filename
                from testing.host.config import SCRAPED_DIR
            except Exception:
                return {"ok": False, "error": "persistence helpers unavailable"}

        url = args.get("url") or args.get("tabUrl") or args.get("tab_url") or ""
        if not url:
            return {"ok": False, "error": "url required"}
        try:
            idx = int(args.get("cell_index") or args.get("cellIndex") or args.get("index"))
        except Exception:
            return {"ok": False, "error": "invalid index"}
        filename = get_safe_filename(url)
        ppath = SCRAPED_DIR / "persistent" / filename
        data = read_json_file(ppath) or {"cells": []}
        cells = list(data.get("cells") or [])
        new_cells = [c for c in cells if int(c.get("index", 0)) != int(idx)]
        # renumber
        for i, c in enumerate(new_cells, start=1):
            try:
                c["index"] = int(i)
            except Exception:
                c["index"] = i
        data["cells"] = new_cells
        try:
            save_persistent_json(data, url)
        except Exception as e:
            return {"ok": False, "error": f"failed to save notebook: {e}"}
        return {"ok": True, "phase": "deleted", "cellIndex": idx}

    inproc_map = {
        "click_cell": _click_inproc,
        "select_cell_by_index": _select_inproc,
        "insert_cell": _insert_inproc,
        "edit_cell_by_index": _edit_inproc,
        "delete_by_index": _delete_inproc,
    }

    for name, schema, desc in candidates:
        try:
            if name in inproc_map:
                _REGISTRY.register(name, schema, desc, inproc_map[name])
                continue
            wrapper = _make_simple_jsonl_wrapper(name)
            _REGISTRY.register(name, schema, desc, wrapper)
        except Exception:
            # If a tool fails to load, skip registration but keep moving
            continue


_register_default_tools()


# Register a simple notebook graph query tool that reads persistent snapshots
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
        {"type": "object", "properties": {"url": {"type": "string"}}},
        "Query the persistent notebook snapshot and return a small graph summary",
        _notebook_graph_wrapper,
    )
except Exception:
    pass


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
        # Curated examples for common tools to improve prompt engineering
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
                for name, prop in props.items():
                    ptype = prop.get("type") if isinstance(prop, dict) else None
                    if ptype == "integer":
                        example[name] = 1
                    elif ptype == "number":
                        example[name] = 1.0
                    elif ptype == "boolean":
                        example[name] = True
                    else:
                        example[name] = "example"
            ex_lines.append(f"{k} example args: {json.dumps(example, ensure_ascii=False)}")
        examples_path.write_text("\n".join(ex_lines), encoding="utf-8")
    except Exception:
        pass


# Generate autogen prompts now
generate_prompt_autogen()


if __name__ == "__main__":
    print("Tool registry contains:", list(_REGISTRY._tools.keys()))
