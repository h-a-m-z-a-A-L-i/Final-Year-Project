import json
import sys
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urlunparse

DATA_ROOT = Path(__file__).parent / "data"
SCRAPED_DIR = DATA_ROOT / "notebooks"

def _normalized_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))
    except Exception:
        return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")

def get_safe_filename(url: str) -> str:
    safe_name = "".join(c if c.isalnum() else "_" for c in _normalized_url(url)).strip("_")
    return f"{safe_name[:200]}.json"

def _system_time_label() -> str:
    return datetime.now().strftime("%I:%M%p").lstrip("0").lower()

def update_cell_execution(cell_index: int, tab_url: str):
    """Update cell execution order and title in notebook JSON."""
    if cell_index is None:
        return
    
    json_path = SCRAPED_DIR / get_safe_filename(tab_url)
    if not json_path.exists():
        return
    
    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        
        cells = data.get("cells", [])
        
        # Find cell by index
        target_cell = None
        for cell in cells:
            if cell.get("index") == cell_index:
                target_cell = cell
                break
        
        if not target_cell:
            return
        
        # Find max execution_order to assign next one
        max_order = 0
        for cell in cells:
            order = cell.get("execution_order")
            if order is not None and isinstance(order, int):
                max_order = max(max_order, order)
        
        # Update execution state
        new_order = max_order + 1 if max_order > 0 else 1
        target_cell["execution_order"] = new_order
        target_cell["execution_title"] = f"Cell executed at {_system_time_label()}"
        
        # Write back atomically
        tmp_path = json_path.with_suffix(json_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp_path.replace(json_path)
        
    except Exception as e:
        pass

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        try:
            cell_index = int(sys.argv[1])
            tab_url = sys.argv[2]
            update_cell_execution(cell_index, tab_url)
        except (ValueError, IndexError):
            pass
