import json
from pathlib import Path
from datetime import datetime, timezone

try:
    from .config import SCRAPED_DIR, LOG_PATH
except Exception:
    try:
        from config import SCRAPED_DIR, LOG_PATH
    except Exception:
        from testing.host.config import SCRAPED_DIR, LOG_PATH

try:
    from .persistence import get_safe_filename
except Exception:
    try:
        from persistence import get_safe_filename
    except Exception:
        from testing.host.persistence import get_safe_filename

# Try to import external dependency modules if available
try:
    from dependency_tracker import DependencyTracker
    from context_builder import ContextBuilder
    _DEP_AVAILABLE = True
    _DEP_FALLBACK = False
except Exception:
    _DEP_AVAILABLE = False
    _DEP_FALLBACK = False


try:
    from .extract_dependencies import DependencyTracker as FallbackDependencyTracker
except Exception:
    try:
        from extract_dependencies import DependencyTracker as FallbackDependencyTracker
    except Exception:
        from testing.host.extract_dependencies import DependencyTracker as FallbackDependencyTracker


class LocalContextBuilder:
    """In-repo context builder using notebook_context (no external packages)."""

    def __init__(self, notebook_url: str, bot_state: dict | None = None):
        self.notebook_url = notebook_url
        self.bot_state = bot_state or {}

    def get_cell_context(self, cell_num: int) -> str:
        try:
            from .notebook_context import get_cell_context_text
        except Exception:
            from notebook_context import get_cell_context_text
        return get_cell_context_text(self.notebook_url, int(cell_num), self.bot_state)


def _build_fallback_graph(notebook_url: str):
    """Build a minimal graph from saved notebook JSON when dependency modules are unavailable."""
    try:
        from .notebook_storage import resolve_storage_key, notebook_paths, load_notebook_snapshot_for_url
    except Exception:
        try:
            from notebook_storage import resolve_storage_key, notebook_paths, load_notebook_snapshot_for_url
        except Exception:
            from testing.host.notebook_storage import resolve_storage_key, notebook_paths, load_notebook_snapshot_for_url

    storage_key = resolve_storage_key(notebook_url)
    filename = notebook_paths(storage_key)["live"].name
    try:
        with LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f"[_build_fallback_graph] URL: {notebook_url} -> key: {storage_key} file: {filename}\n")
    except Exception:
        pass
    data, _ = load_notebook_snapshot_for_url(notebook_url)
    if not isinstance(data, dict):
        return None
    try:
        cells = data.get('cells', [])

        tracker = FallbackDependencyTracker()
        for cell in cells:
            if cell.get('type') == 'code':
                idx = cell.get('index', 0)
                code = cell.get('input', '')
                tracker.add_cell(idx, code)

        tracker.compute_graph()

        reverse_deps = {idx: [] for idx in tracker.symbol_table.keys()}
        for idx, deps in tracker.dependencies.items():
            for d in deps:
                if d in reverse_deps:
                    reverse_deps[d].append(idx)

        graph = []
        for cell in cells:
            idx = cell.get('index', 0)
            code = str(cell.get('input', ''))
            cell_type = cell.get('type', 'code')
            if cell_type == 'code':
                deps = tracker.dependencies.get(idx, [])
                rdeps = reverse_deps.get(idx, [])
            else:
                deps = []
                rdeps = []
            graph.append({
                'cell_number': idx,
                'input_preview': code[:120],
                'dependencies': deps,
                'reverse_dependencies': rdeps
            })
        return graph
    except Exception as e:
        try:
            with LOG_PATH.open('a', encoding='utf-8') as f:
                f.write(f"Fallback graph build error: {e}\n")
        except Exception:
            pass
        return None


class DependencyManager:
    """Manages ContextBuilder instances for each notebook, loading from SCRAPED_DIR."""
    def __init__(self, json_dir: Path):
        self.json_dir = json_dir
        self._cache = {}
        self._bot_state: dict = {}

    def set_bot_state(self, state: dict | None):
        self._bot_state = state or {}

    def get_builder(self, notebook_url: str):
        if not notebook_url:
            return None

        if _DEP_AVAILABLE:
            try:
                from .notebook_storage import resolve_storage_key, notebook_paths
            except Exception:
                from notebook_storage import resolve_storage_key, notebook_paths
            storage_key = resolve_storage_key(notebook_url)
            json_path = notebook_paths(storage_key)["persistent"]
            if not json_path.is_file():
                json_path = notebook_paths(storage_key)["live"]
            if not json_path.is_file():
                return None
            filename = json_path.name

            mtime = json_path.stat().st_mtime
            if filename not in self._cache or self._cache[filename]['mtime'] != mtime:
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    cells = data.get('cells', [])
                    tracker = DependencyTracker()
                    cells_data = {}
                    for cell in cells:
                        idx = cell.get('index', 0)
                        code = cell.get('input', '')
                        output = cell.get('output', '')
                        cells_data[idx] = {'code': code, 'output': output}
                        tracker.update_cell(idx, code)
                    tracker.update_all_reverse_dependencies()
                    self._cache[filename] = {
                        'builder': ContextBuilder(tracker, cells_data),
                        'mtime': mtime,
                        'cell_count': len(cells_data)
                    }
                except Exception as e:
                    try:
                        with LOG_PATH.open('a', encoding='utf-8') as f:
                            f.write(f"Failed to build graph: {e}\n")
                    except Exception:
                        pass
                    return None
            return self._cache[filename]['builder']

        # Graph/chat context uses notebook_context.build_graph_list / pack_context in-repo.
        return None
