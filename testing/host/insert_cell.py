"""Cell insertion tool.

Workflow:
1. Click a cell at the given index (selects it)
2. Display menu: insert (a)bove or (b)elow?
3. Send keyboard key (a or b) to notebook
4. Poll notebook metadata to confirm cell was inserted
"""
import argparse
import json
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_META = ROOT / "data" / "meta"
DATA_NOTEBOOKS = ROOT / "data" / "notebooks"
BOT_COMMANDS_PATH = DATA_META / "bot_commands.jsonl"
BOT_RESULTS_PATH = DATA_META / "bot_results.jsonl"


def _append_jsonl(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_all_jsonl(path: Path):
    """Read entire JSONL file and return list of valid JSON objects."""
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


def _wait_for_request_result(request_id: str, timeout_seconds: float):
    """Poll bot_results.jsonl for exact requestId match."""
    deadline = time.time() + max(0.5, timeout_seconds)
    seen_lines = set()
    
    while time.time() < deadline:
        results = _read_all_jsonl(BOT_RESULTS_PATH)
        for event in results:
            event_id = id(event)
            if event_id not in seen_lines and event.get("requestId") == request_id:
                seen_lines.add(event_id)
                return event
        time.sleep(0.2)
    
    return None


def _queue_command(cmd: dict):
    """Queue a command to bot_commands.jsonl."""
    _append_jsonl(BOT_COMMANDS_PATH, cmd)


def _build_click_command(request_id: str, tab_id: int | None, cell_index: int, url: str):
    """Build command to click a cell by index."""
    cmd = {
        "action": "click",
        "requestId": request_id,
        "cellIndex": cell_index,
        "scrollIntoView": True,
        "runCell": False,
    }
    if tab_id is not None:
        cmd["tabId"] = tab_id
    if url:
        cmd["url"] = url
    return cmd


def _build_send_key_command(request_id: str, tab_id: int | None, key: str, url: str):
    """Build command to send keyboard key to notebook."""
    cmd = {
        "action": "send_key",
        "requestId": request_id,
        "key": key,
    }
    if tab_id is not None:
        cmd["tabId"] = tab_id
    if url:
        cmd["url"] = url
    return cmd


def _get_notebook_cell_count(url_hash: str) -> int | None:
    """Read notebook metadata and return current cell count."""
    notebook_path = None
    for nb_file in DATA_NOTEBOOKS.glob("*.json"):
        if url_hash in nb_file.name:
            notebook_path = nb_file
            break
    
    if not notebook_path or not notebook_path.exists():
        return None
    
    try:
        with notebook_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        cells = data.get("cells", [])
        return len(cells)
    except Exception:
        return None


def _url_to_hash(url: str) -> str:
    """Convert URL to notebook file hash (URL-encoded filename prefix)."""
    return url.replace("://", "___").replace("/", "_").replace("?", "_")


def main():
    parser = argparse.ArgumentParser(description="Insert a cell above or below a target cell")
    parser.add_argument("index", type=int, help="Cell index to insert next to (0-based)")
    parser.add_argument("--tab-id", type=int, default=None, help="Optional tab id")
    parser.add_argument("--url", default="", help="Optional notebook URL (for metadata polling)")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout in seconds")
    args = parser.parse_args()

    if args.index < 0:
        print(json.dumps({"ok": False, "error": "index must be >= 0"}, ensure_ascii=False))
        return

    # ===== PHASE 1: Click the target cell =====
    print(json.dumps({"ok": True, "phase": "selecting_cell", "cellIndex": args.index}, ensure_ascii=False))
    
    click_request_id = str(uuid.uuid4())
    click_cmd = _build_click_command(click_request_id, args.tab_id, args.index, args.url)
    
    _queue_command(click_cmd)
    click_result = _wait_for_request_result(click_request_id, args.timeout)
    
    if not click_result:
        print(json.dumps({
            "ok": False,
            "phase": "cell_selection",
            "error": "Timeout waiting for cell click result"
        }, ensure_ascii=False))
        return
    
    inner_result = click_result.get("result", {})
    if not inner_result.get("ok", False):
        print(json.dumps({
            "ok": False,
            "phase": "cell_selection",
            "error": "Cell click failed",
            "details": click_result
        }, ensure_ascii=False))
        return
    
    # Extract tab ID from response
    resolved_tab_id = args.tab_id
    if isinstance(inner_result.get("tabId"), int):
        resolved_tab_id = inner_result["tabId"]
    elif isinstance(click_result.get("tabId"), int):
        resolved_tab_id = click_result["tabId"]
    
    if resolved_tab_id is None:
        print(json.dumps({
            "ok": False,
            "phase": "cell_selection",
            "error": "Could not resolve tab ID"
        }, ensure_ascii=False))
        return
    
    print(json.dumps({
        "ok": True,
        "phase": "cell_selected",
        "cellIndex": args.index,
        "tabId": resolved_tab_id
    }, ensure_ascii=False))
    
    # ===== PHASE 2: Ask user for insertion direction =====
    while True:
        try:
            choice = input("\nInsert (a)bove or (b)elow? [a/b]: ").strip().lower()
            if choice in {"a", "b"}:
                break
            else:
                print("Invalid choice. Enter 'a' for above or 'b' for below.")
        except KeyboardInterrupt:
            print("\nAborted.")
            return
    
    # ===== PHASE 3: Send keyboard key =====
    print(json.dumps({
        "ok": True,
        "phase": "sending_key",
        "direction": "above" if choice == "a" else "below",
        "key": choice
    }, ensure_ascii=False))
    
    key_request_id = str(uuid.uuid4())
    key_cmd = _build_send_key_command(key_request_id, resolved_tab_id, choice, args.url)
    
    _queue_command(key_cmd)
    key_result = _wait_for_request_result(key_request_id, args.timeout)
    
    if not key_result:
        print(json.dumps({
            "ok": False,
            "phase": "key_send",
            "error": "Timeout waiting for key send result"
        }, ensure_ascii=False))
        return
    
    inner_key_result = key_result.get("result", {})
    if not inner_key_result.get("ok", False):
        print(json.dumps({
            "ok": False,
            "phase": "key_send",
            "error": "Key send failed",
            "details": key_result
        }, ensure_ascii=False))
        return
    
    print(json.dumps({
        "ok": True,
        "phase": "key_sent",
        "key": choice
    }, ensure_ascii=False))
    
    # ===== PHASE 4: Poll metadata for confirmation =====
    if not args.url:
        print(json.dumps({
            "ok": True,
            "phase": "complete",
            "note": "No URL provided for metadata confirmation"
        }, ensure_ascii=False))
        return
    
    print(json.dumps({
        "ok": True,
        "phase": "confirming_insertion",
        "url": args.url
    }, ensure_ascii=False))
    
    # Get initial cell count
    url_hash = _url_to_hash(args.url)
    initial_count = _get_notebook_cell_count(url_hash)
    
    if initial_count is None:
        print(json.dumps({
            "ok": True,
            "phase": "complete",
            "note": "Could not access metadata file for confirmation, but key was sent"
        }, ensure_ascii=False))
        return
    
    # Poll for new cell count
    deadline = time.time() + max(1.0, args.timeout * 0.5)
    confirmed = False
    
    while time.time() < deadline:
        current_count = _get_notebook_cell_count(url_hash)
        if current_count is not None and current_count > initial_count:
            confirmed = True
            break
        time.sleep(0.5)
    
    if confirmed:
        print(json.dumps({
            "ok": True,
            "phase": "complete",
            "status": "Cell inserted successfully",
            "previousCellCount": initial_count,
            "newCellCount": current_count,
            "direction": "above" if choice == "a" else "below"
        }, ensure_ascii=False))
    else:
        print(json.dumps({
            "ok": True,
            "phase": "complete",
            "status": "Key was sent but metadata confirmation pending (may appear after auto-save)",
            "initialCellCount": initial_count,
            "note": "Kaggle auto-saves; check notebook directly for confirmation"
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
