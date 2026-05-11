"""Shared helpers for notebook bot CLI tools."""
import json
import time
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DATA_META = ROOT / "data" / "meta"
DATA_NOTEBOOKS = ROOT / "data" / "notebooks"
BOT_COMMANDS_PATH = DATA_META / "bot_commands.jsonl"
BOT_RESULTS_PATH = DATA_META / "bot_results.jsonl"
DEFAULT_TAB_ID = 2015853912


def append_jsonl(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def queue_command(payload: dict):
    append_jsonl(BOT_COMMANDS_PATH, payload)


def read_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def wait_for_request_result(request_id: str, timeout_seconds: float):
    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        for event in read_jsonl(BOT_RESULTS_PATH):
            if event.get("requestId") == request_id:
                return event
        time.sleep(0.2)
    return None


def url_to_hash(url: str) -> str:
    return str(url or "").replace("://", "___").replace("/", "_").replace("?", "_")


def find_notebook_metadata(url: str):
    notebook_hash = url_to_hash(url)
    for nb_file in DATA_NOTEBOOKS.glob("*.json"):
        if notebook_hash in nb_file.name:
            return nb_file
    return None


def read_notebook_cell_count(url: str):
    notebook_path = find_notebook_metadata(url)
    if not notebook_path or not notebook_path.exists():
        return None
    try:
        with notebook_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data.get("cells", []))
    except Exception:
        return None
