import json
import sys
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urlunparse

DATA_ROOT = Path(__file__).parent / "data"
SCRAPED_DIR = DATA_ROOT / "notebooks"
LOG_FILE = DATA_ROOT / "logs" / "update_exec.log"

def log_msg(msg):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except:
        pass

def _normalized_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))
    except:
        return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")

def get_safe_filename(url: str) -> str:
    safe_name = "".join(c if c.isalnum() else "_" for c in _normalized_url(url)).strip("_")
    return f"{safe_name[:200]}.json"

def _system_time_label() -> str:
    return datetime.now().strftime("%I:%M%p").lstrip("0").lower()

def update_cell_execution(cell_index: int, tab_url: str):
    """Update cell execution order and title in notebook JSON."""
    if cell_index is None:
        log_msg(f"SKIP cell_index=None")
        pass
        return
    
    filename = get_safe_filename(tab_url)
    json_path = SCRAPED_DIR / get_safe_filename(tab_url)
    log_msg(f"START cell={cell_index} url={tab_url} file={filename} exists={json_path.exists()}")
    
    if not json_path.exists():
        log_msg(f"FAIL JSON not found: {json_path}")
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
            log_msg(f"FAIL cell_index {cell_index} not in JSON")
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
        
        log_msg(f"SUCCESS updated cell {cell_index} order={new_order}")
        
    except Exception as e:
        log_msg(f"ERROR {type(e).__name__}: {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        try:
            cell_index = int(sys.argv[1])
            tab_url = sys.argv[2]
            update_cell_execution(cell_index, tab_url)
        except (ValueError, IndexError):
            log_msg(f"ERROR Invalid args: {sys.argv}")
