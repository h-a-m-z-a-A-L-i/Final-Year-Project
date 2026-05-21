import json
from pathlib import Path
from datetime import datetime, timezone
import uuid
try:
    import persistence
except Exception:
    persistence = None
try:
    from .config import SCRAPED_DIR, HASHES_PATH, EXECUTION_STATE_PATH, LOG_PATH
except Exception:
    try:
        from config import SCRAPED_DIR, HASHES_PATH, EXECUTION_STATE_PATH, LOG_PATH
    except Exception:
        from testing.host.config import SCRAPED_DIR, HASHES_PATH, EXECUTION_STATE_PATH, LOG_PATH


def read_json_file(file_path: Path) -> dict | None:
    if persistence:
        return persistence.read_json_file(file_path)
    file_path = Path(file_path)
    if not file_path.is_file():
        return None
    try:
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _atomic_write_json(file_path: Path, data):
    if persistence:
        return persistence.atomic_write_json(file_path, data)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(file_path)


def save_json(data, tab_url):
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    filename = get_safe_filename(tab_url)
    filepath = SCRAPED_DIR / filename
    _atomic_write_json(filepath, data)
    return filepath


def _live_dir() -> Path:
    d = SCRAPED_DIR / "live"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persistent_dir() -> Path:
    d = SCRAPED_DIR / "persistent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_live_json(data, tab_url):
    filename = get_safe_filename(tab_url)
    path = _live_dir() / filename
    _atomic_write_json(path, data)
    return path


def save_persistent_json(data, tab_url):
    filename = get_safe_filename(tab_url)
    path = _persistent_dir() / filename
    _atomic_write_json(path, data)
    legacy_path = SCRAPED_DIR / filename
    _atomic_write_json(legacy_path, data)
    return path


def get_safe_filename(url: str) -> str:
    safe_name = "".join(c if c.isalnum() else "_" for c in (url or "")).strip("_")
    return f"{safe_name[:200]}.json"


def _load_hashes() -> dict:
    data = read_json_file(HASHES_PATH)
    if data is not None and isinstance(data, dict):
        return data
    if HASHES_PATH.is_file():
        try:
            corrupt = HASHES_PATH.with_suffix(HASHES_PATH.suffix + f".corrupt.{int(datetime.now(timezone.utc).timestamp())}")
            HASHES_PATH.replace(corrupt)
        except Exception:
            pass
        try:
            with LOG_PATH.open('a', encoding='utf-8') as f:
                f.write(f"Invalid hashes file moved to: {corrupt.name}\n")
        except Exception:
            pass
    return {}


def _save_hashes(hashes: dict):
    _atomic_write_json(HASHES_PATH, hashes)


def _load_execution_state() -> dict:
    data = read_json_file(EXECUTION_STATE_PATH)
    if isinstance(data, dict):
        return data
    return {}


def _save_execution_state(state: dict):
    _atomic_write_json(EXECUTION_STATE_PATH, state)
