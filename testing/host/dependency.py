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


def _build_fallback_graph(notebook_url: str):
    """Build a minimal graph from saved notebook JSON when dependency modules are unavailable."""
    filename = get_safe_filename(notebook_url)
    try:
        with LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f"[_build_fallback_graph] URL: {notebook_url} -> Filename: {filename}\n")
    except Exception:
        pass
    candidates = [SCRAPED_DIR / 'persistent' / filename]
    json_path = None
    for p in candidates:
        try:
            with LOG_PATH.open('a', encoding='utf-8') as f:
                f.write(f"[_build_fallback_graph] Checking: {p} (exists: {p.exists()})\n")
        except Exception:
            pass
        if p.exists():
            json_path = p
            break
    if not json_path:
        return None
    try:
        with json_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
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

    def get_builder(self, notebook_url: str):
        if not _DEP_AVAILABLE or not notebook_url:
            return None
        filename = get_safe_filename(notebook_url)
        candidates = [self.json_dir / 'persistent' / filename]
        json_path = None
        for p in candidates:
            if p.exists():
                json_path = p
                break
        if not json_path:
            return None

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
