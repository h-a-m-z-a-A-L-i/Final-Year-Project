import json
import sys
from typing import Optional
from pathlib import Path
import persistence
from datetime import datetime
from urllib.parse import urlparse, urlunparse

DATA_ROOT = Path(__file__).parent / "data"
SCRAPED_DIR = DATA_ROOT / "notebooks"
LOG_FILE = DATA_ROOT / "logs" / "update_exec.log"

LIVE_DIR = SCRAPED_DIR / "live"
PERSISTENT_DIR = SCRAPED_DIR / "persistent"

# use canonical atomic writer from persistence module

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
    return persistence.get_safe_filename(url)

def _system_time_label() -> str:
    return datetime.now().strftime("%I:%M%p").lstrip("0").lower()

def update_cell_execution(cell_index: int, tab_url: str, exec_timestamp_ms: Optional[int] = None, exec_order: Optional[int] = None):
    """Update cell execution order and title in notebook JSON using extension's timestamp."""
    if cell_index is None:
        log_msg(f"SKIP cell_index=None")
        return
    try:
        cell_index = int(cell_index)
    except Exception:
        log_msg(f"SKIP invalid cell_index={cell_index}")
        return
    if cell_index <= 0:
        log_msg(f"SKIP invalid cell_index={cell_index}")
        return
    
    filename = get_safe_filename(tab_url)
    json_path = PERSISTENT_DIR / filename
    if not json_path.exists():
        json_path = SCRAPED_DIR / filename
    log_msg(f"START cell={cell_index} url={tab_url} file={filename} ts={exec_timestamp_ms} exists={json_path.exists()}")

    try:
        data = persistence.read_json_file(json_path)
        if data is None:
            data = {
                "tabUrl": tab_url,
                "title": "notebook",
                "lastUpdated": datetime.now().isoformat(),
                "cells": [],
            }
        
        cells = data.get("cells", [])
        
        # Find cell by index
        target_cell = None
        for cell in cells:
            if cell.get("index") == cell_index:
                target_cell = cell
                break
        
        if not target_cell:
            while len(cells) < cell_index:
                cells.append({
                    "index": len(cells) + 1,
                    "input": "",
                    "output": "",
                    "execution_order": None,
                    "execution_title": "",
                })
            target_cell = cells[cell_index - 1]
        
        # Convert extension's millisecond timestamp to ISO format
        if exec_timestamp_ms:
            try:
                exec_timestamp_ms = int(exec_timestamp_ms)
                exec_dt = datetime.fromtimestamp(exec_timestamp_ms / 1000.0)
                exec_time = exec_dt.strftime("%I:%M%p").lstrip("0").lower()
                exec_iso = exec_dt.isoformat()
            except:
                exec_time = _system_time_label()
                exec_iso = datetime.now().isoformat()
        else:
            exec_time = _system_time_label()
            exec_iso = datetime.now().isoformat()
            
        if exec_order is not None:
            target_cell["execution_order"] = exec_order
            
        target_cell["execution_title"] = f"Cell executed at {exec_time}"
        
        # Print to terminal for verification
        print(f"[EXEC-UPDATE] Cell {cell_index}: order={exec_order}, title='{target_cell['execution_title']}'")
        
        # Write back atomically to legacy, live, and persistent locations
        try:
            persistence.atomic_write_json(json_path, data)
            persistence.atomic_write_json(LIVE_DIR / filename, data)
            persistence.atomic_write_json(PERSISTENT_DIR / filename, data)
            log_msg(f"SUCCESS updated cell {cell_index} order={exec_order} time={exec_time}")
        except Exception as e:
            log_msg(f"WRITE-ERROR {type(e).__name__}: {e}")
            raise
        
    except Exception as e:
        log_msg(f"ERROR {type(e).__name__}: {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        try:
            cell_index = int(sys.argv[1])
            tab_url = sys.argv[2]
            exec_timestamp_ms = None
            exec_order = None
            if len(sys.argv) >= 4:
                try:
                    exec_timestamp_ms = int(sys.argv[3])
                except:
                    pass
            if len(sys.argv) >= 5:
                try:
                    exec_order = int(sys.argv[4])
                except:
                    pass
            update_cell_execution(cell_index, tab_url, exec_timestamp_ms, exec_order)
        except (ValueError, IndexError):
            log_msg(f"ERROR Invalid args: {sys.argv}")
