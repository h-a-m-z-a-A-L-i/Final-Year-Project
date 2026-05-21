import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse


BASE_DATA = Path(__file__).resolve().parent / "data"
SCRAPED_DIR = BASE_DATA / "notebooks"


def _normalized_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.split('#', 1)[0].split('?', 1)[0].rstrip('/')
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), (parsed.path or '').rstrip('/'), "", "", ""))
    except Exception:
        return raw.split('#', 1)[0].split('?', 1)[0].rstrip('/')


def get_safe_filename(url: str) -> str:
    safe_name = "".join(c if c.isalnum() else "_" for c in _normalized_url(url)).strip("_")
    return f"{safe_name[:200]}.json"


import threading

_FILE_LOCKS = {}
_FILE_LOCKS_LOCK = threading.Lock()

def get_file_lock(filepath):
    filepath = str(Path(filepath).resolve())
    with _FILE_LOCKS_LOCK:
        if filepath not in _FILE_LOCKS:
            _FILE_LOCKS[filepath] = threading.Lock()
        return _FILE_LOCKS[filepath]


def read_json_file(file_path: Path) -> dict | None:
    """Thread-safe read of JSON file with retry loop for Windows file sharing conflicts."""
    file_path = Path(file_path)
    if not file_path.is_file():
        return None
    lock = get_file_lock(file_path)
    with lock:
        for attempt in range(5):
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                if attempt < 4:
                    time.sleep(0.05 * (attempt + 1))
                    continue
        return None


def atomic_write_json(file_path: Path, data):
    """Atomically write JSON to file: write to tmp, fsync, then replace."""
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    
    lock = get_file_lock(file_path)
    with lock:
        # Write to temp file
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                # fsync may not be available on all platforms or for some file-like objects
                pass
        
        # Replace target file atomically; retry on transient failures (Windows can lock files)
        max_retries = 10
        for attempt in range(max_retries):
            try:
                tmp_path.replace(file_path)
                return
            except Exception:
                try:
                    os.replace(str(tmp_path), str(file_path))
                    return
                except Exception:
                    if attempt < max_retries - 1:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    
                    # Last resort: try non-atomic overwrite to avoid losing data
                    try:
                        with file_path.open("w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                            try:
                                f.flush()
                                os.fsync(f.fileno())
                            except Exception:
                                pass
                        # cleanup tmp if still exists
                        if tmp_path.exists():
                            try:
                                tmp_path.unlink()
                            except Exception:
                                pass
                        return
                    except Exception:
                        # give up
                        if tmp_path.exists():
                            try:
                                tmp_path.unlink()
                            except Exception:
                                pass
                        raise


def save_json(data, tab_url, scraped_dir: Path | None = None):
    scraped = SCRAPED_DIR if scraped_dir is None else Path(scraped_dir)
    scraped.mkdir(parents=True, exist_ok=True)
    filename = get_safe_filename(tab_url)
    filepath = scraped / filename
    atomic_write_json(filepath, data)
    return filepath


def save_live_json(data, tab_url, scraped_dir: Path | None = None):
    scraped = SCRAPED_DIR if scraped_dir is None else Path(scraped_dir)
    live = scraped / "live"
    live.mkdir(parents=True, exist_ok=True)
    filename = get_safe_filename(tab_url)
    path = live / filename
    atomic_write_json(path, data)
    return path


def save_persistent_json(data, tab_url, scraped_dir: Path | None = None):
    scraped = SCRAPED_DIR if scraped_dir is None else Path(scraped_dir)
    persistent = scraped / "persistent"
    persistent.mkdir(parents=True, exist_ok=True)
    filename = get_safe_filename(tab_url)
    path = persistent / filename
    atomic_write_json(path, data)
    # Backwards-compat: keep legacy root file in sync
    legacy = scraped / filename
    atomic_write_json(legacy, data)
    return path
