import json
import ast
import re
from pathlib import Path
from typing import Dict, List, Set, Optional

class DependencyTracker:
    """
    Cleanly extracts dependencies between Python code cells.
    Logic:
    1. Parse AST to find 'defines' (Assign, FunctionDef, ClassDef, Import)
    2. Parse AST to find 'uses' (Name Load)
    3. Match 'uses' in Cell B with 'defines' in Cell A.
    """
    def __init__(self):
        self.symbol_table = {}  # cell_id -> {defines: set, uses: set}
        self.dependencies = {}  # cell_id -> list of cell_ids

    def _parse_symbols(self, code: str) -> Dict[str, Set[str]]:
        symbols = {'defines': set(), 'uses': set()}
        if not code.strip(): return symbols
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name): symbols['defines'].add(t.id)
                elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    symbols['defines'].add(node.name)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        symbols['defines'].add(alias.asname or alias.name.split('.')[0])
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id not in dir(__builtins__):
                        symbols['uses'].add(node.id)
        except SyntaxError:
            # Fallback for dynamic/incomplete code
            defs = re.findall(r'^(\w+)\s*=', code, re.M)
            symbols['defines'].update(defs)
        return symbols

    def add_cell(self, cell_id: int, code: str):
        self.symbol_table[cell_id] = self._parse_symbols(code)

    def compute_graph(self):
        self.dependencies = {}
        for idx, syms in self.symbol_table.items():
            deps = []
            used = syms['uses']
            for other_idx, other_syms in self.symbol_table.items():
                if idx == other_idx: continue
                if used.intersection(other_syms['defines']):
                    deps.append(other_idx)
            self.dependencies[idx] = sorted(deps)

def run_extraction(json_path: Path):
    if not json_path.exists():
        print(f"Error: {json_path} not found.")
        return

    print(f"--- Extracting Dependencies from: {json_path.name} ---")
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tracker = DependencyTracker()
    cells = data.get('cells', [])
    
    # 1. Load symbols
    for cell in cells:
        tracker.add_cell(cell['index'], cell['input'])
    
    # 2. Compute links
    tracker.compute_graph()

    # 3. Print results
    for idx, deps in tracker.dependencies.items():
        cell_preview = next((c['input'][:50].replace('\n',' ') for c in cells if c['index'] == idx), "")
        dep_str = ", ".join(f"Cell {d}" for d in deps) if deps else "None"
        print(f"[{idx}] {cell_preview}...")
        print(f"    Depends on: {dep_str}\n")

if __name__ == "__main__":
    # Example: Run on the first found JSON in scraped_data
    scraped_dir = Path(__file__).parent / "scraped_data"
    jsons = list(scraped_dir.glob("*.json"))
    if jsons:
        run_extraction(jsons[0])
    else:
        print("No JSON files found in scraped_data/.")
