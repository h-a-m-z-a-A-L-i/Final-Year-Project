"""Simple cell click bot client.

Writes a command into data/meta/bot_commands.jsonl.
host.py consumes it and dispatches CLICK_CELL_BY_INDEX or CLICK_SELECTOR to the extension.
"""
import argparse
import json
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_META = ROOT / "data" / "meta"
BOT_COMMANDS_PATH = DATA_META / "bot_commands.jsonl"
BOT_RESULTS_PATH = DATA_META / "bot_results.jsonl"



def _append_jsonl(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_results_since(path: Path, since_size: int):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        f.seek(since_size)
        raw = f.read()
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _wait_for_request_result(request_id: str, before_size: int, timeout_seconds: float):
    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        for event in _read_results_since(BOT_RESULTS_PATH, before_size):
            if event.get("requestId") == request_id:
                return event
        time.sleep(0.2)
    return None


def _queue_command(cmd: dict):
    _append_jsonl(BOT_COMMANDS_PATH, cmd)


def _make_request_id():
    return str(uuid.uuid4())


def _build_click_command(request_id: str, tab_id: int | None, cell_index: int, url: str):
    cmd = {
        "action": "click",
        "requestId": request_id,
        "cellIndex": cell_index,
        "scrollIntoView": True,
    }
    if tab_id is not None:
        cmd["tabId"] = tab_id
    if url:
        cmd["url"] = url
    return cmd


def _build_selector_command(request_id: str, tab_id: int | None, selector: str, url: str):
    cmd = {
        "action": "click_selector",
        "requestId": request_id,
        "selector": selector,
    }
    if tab_id is not None:
        cmd["tabId"] = tab_id
    if url:
        cmd["url"] = url
    return cmd


def main():
    parser = argparse.ArgumentParser(description="Queue a click-by-index or selector-based command for host bot watcher")
    # New preferred style: click_cell.py <cell_index> [--tab-id <id>]
    # Backward-compatible style: click_cell.py <tab_id> <cell_index>
    parser.add_argument("first", type=int, nargs="?", default=None, help="Cell index (preferred) or tab id (legacy two-positional mode)")
    parser.add_argument("second", type=int, nargs="?", default=None, help="Legacy mode second positional: cell index")
    parser.add_argument("--tab-id", type=int, default=None, help="Optional tab id; defaults to last active notebook tab")
    parser.add_argument("--url", default="", help="Optional notebook URL")
    parser.add_argument("--selector", default="", help="Optional CSS selector to click instead of a cell index")
    parser.add_argument("--interactive", action="store_true", help="Prompt for the cell index when no positional index is provided")
    parser.add_argument("--timeout", type=float, default=8.0, help="Seconds to wait for result")
    args = parser.parse_args()

    if args.selector.strip():
        tab_id = args.tab_id
        cell_index = None
    elif args.second is not None:
        # Legacy mode: first=tab_id second=cell_index
        if args.first is None:
            print(json.dumps({"ok": False, "error": "tab_id is required in legacy mode"}, ensure_ascii=False))
            return
        tab_id = args.first
        cell_index = args.second
    else:
        # Preferred mode: first=cell_index and optional --tab-id
        tab_id = args.tab_id
        cell_index = args.first

    def _process_single_cell(idx, tab_id):
        request_id = str(uuid.uuid4())
        before = BOT_RESULTS_PATH.stat().st_size if BOT_RESULTS_PATH.exists() else 0
        cmd = _build_click_command(request_id, tab_id, idx, args.url)
        
        _queue_command(cmd)
        result = _wait_for_request_result(request_id, before, args.timeout)
        if result is None:
            print(json.dumps({
                "ok": False,
                "requestId": request_id,
                "error": "Timed out waiting for bot result. Ensure host.py is running and extension is connected.",
            }, ensure_ascii=False))
            return None

        inner_result = result.get("result", {})
        if not inner_result.get("ok", False):
            print(json.dumps(result, ensure_ascii=False))
            return None

        resolved_tab_id = inner_result.get("tabId") if isinstance(inner_result.get("tabId"), int) else result.get("tabId")
        if resolved_tab_id is None:
            resolved_tab_id = tab_id
            
        print(json.dumps({"ok": True, "phase": "cell_selected", "tabId": resolved_tab_id, "cellIndex": idx}, ensure_ascii=False))
        print(json.dumps({
            "ok": bool(inner_result.get("ok", False)),
            "cellClick": result,
        }, ensure_ascii=False))
        
        return resolved_tab_id

    # If selector is provided, just run it once and exit
    if args.selector.strip():
        if args.tab_id is None:
            print(json.dumps({"ok": False, "error": "--tab-id is required when using --selector"}, ensure_ascii=False))
            return
            
        request_id = str(uuid.uuid4())
        before = BOT_RESULTS_PATH.stat().st_size if BOT_RESULTS_PATH.exists() else 0
        cmd = _build_selector_command(request_id, args.tab_id, args.selector.strip(), args.url)
        _queue_command(cmd)
        
        result = _wait_for_request_result(request_id, before, args.timeout)
        if result is None:
            print(json.dumps({"ok": False, "error": "Timeout"}, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False))
        return

    # Handle positional arguments first if provided (legacy or new style)
    initial_cells = []
    current_tab_id = args.tab_id

    if args.second is not None:
        if args.first is None:
            print(json.dumps({"ok": False, "error": "tab_id is required in legacy mode"}, ensure_ascii=False))
            return
        current_tab_id = args.first
        initial_cells.append(args.second)
    elif args.first is not None:
        initial_cells.append(args.first)

    # Process any cells provided via command line
    for idx in initial_cells:
        if idx < 0:
            print(json.dumps({"ok": False, "error": "cell_index must be >= 0"}, ensure_ascii=False))
            continue
        new_tab_id = _process_single_cell(idx, current_tab_id)
        if new_tab_id is not None:
            current_tab_id = new_tab_id

    # Enter continuous interactive loop
    try:
        while True:
            raw_value = input("\nEnter cell index to run (or Ctrl+C to quit): ").strip()
            if not raw_value:
                continue
                
            # Allow multiple comma-separated or space-separated indices: "1 2 3" or "1,2,3"
            parts = raw_value.replace(",", " ").split()
            for part in parts:
                try:
                    idx = int(part)
                    if idx < 0:
                        print(f"Skipping {idx}: index must be >= 0")
                        continue
                    new_tab_id = _process_single_cell(idx, current_tab_id)
                    if new_tab_id is not None:
                        current_tab_id = new_tab_id
                        
                    # Small delay between multiple executions to allow Kaggle to process
                    time.sleep(0.3)
                except ValueError:
                    print(f"Skipping '{part}': must be an integer")
    except KeyboardInterrupt:
        print("\nExiting continuous mode.")
        return

if __name__ == "__main__":
    main()

