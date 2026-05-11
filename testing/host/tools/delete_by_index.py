"""Cell deletion tool.
Selects a cell by index and then deletes it by sending 'd d' keystrokes.
"""
import argparse
import json
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_META = ROOT / "data" / "meta"
BOT_COMMANDS_PATH = DATA_META / "bot_commands.jsonl"
BOT_RESULTS_PATH = DATA_META / "bot_results.jsonl"


def _append_jsonl(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_all_jsonl(path: Path):
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
    _append_jsonl(BOT_COMMANDS_PATH, cmd)


def _build_click_command(request_id: str, tab_id: int | None, cell_index: int, url: str):
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


def _build_send_keys_command(request_id: str, tab_id: int | None, keys: str, url: str):
    cmd = {
        "action": "send_keys",
        "requestId": request_id,
        "keys": keys,           # e.g., "d d"
    }
    if tab_id is not None:
        cmd["tabId"] = tab_id
    if url:
        cmd["url"] = url
    return cmd


def _build_send_key_command(request_id: str, tab_id: int | None, key: str, url: str):
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
    parser = argparse.ArgumentParser(description="Delete a cell by index via keystrokes")
    parser.add_argument("index", type=int, nargs="?", default=None, help="Cell index to delete")
    parser.add_argument("--tab-id", type=int, default=None, help="Optional tab id")
    parser.add_argument("--url", default="", help="Optional notebook URL")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout in seconds")
    args = parser.parse_args()

    def _process_delete(idx, tab_id):
        print(json.dumps({"ok": True, "phase": "selecting_cell", "cellIndex": idx}, ensure_ascii=False))

        # 1. Select the cell
        click_request_id = str(uuid.uuid4())
        click_cmd = _build_click_command(click_request_id, tab_id, idx, args.url)

        _queue_command(click_cmd)
        click_result = _wait_for_request_result(click_request_id, args.timeout)

        if not click_result:
            print(json.dumps({"ok": False, "phase": "cell_selection", "error": "Timeout waiting for cell click result"}, ensure_ascii=False))
            return tab_id

        inner_result = click_result.get("result", {})
        if not inner_result.get("ok", False):
            print(json.dumps({"ok": False, "phase": "cell_selection", "error": "Cell click failed", "details": click_result}, ensure_ascii=False))
            return tab_id

        resolved_tab_id = tab_id
        if isinstance(inner_result.get("tabId"), int):
            resolved_tab_id = inner_result["tabId"]
        elif isinstance(click_result.get("tabId"), int):
            resolved_tab_id = click_result["tabId"]

        print(json.dumps({"ok": True, "phase": "cell_selected", "cellIndex": idx, "tabId": resolved_tab_id}, ensure_ascii=False))
        print(json.dumps({"ok": True, "phase": "sending_delete_keystrokes"}, ensure_ascii=False))

        # Wait a short moment to allow UI to update selection state before clicking delete
        time.sleep(0.5)

        # 2. Try several selectors targeted at the selected cell, falling back to 'dd' keys
        candidates = [
            f'[data-windowed-list-index="{idx}"] div > div > div > button.cell-context-menu-icon-button.delete',
            f'[data-windowed-list-index="{idx}"] button[aria-label*="Delete"]',
            f'[data-windowed-list-index="{idx}"] .cell-context-menu-icon-button.delete',
            'button.cell-context-menu-icon-button.delete',
            'button[aria-label*="Delete"]',
        ]

        sel_success = False
        last_sel_error = None
        for selector in candidates:
            sel_request_id = str(uuid.uuid4())
            sel_cmd = _build_selector_command(sel_request_id, resolved_tab_id, selector, args.url)
            _queue_command(sel_cmd)
            sel_result = _wait_for_request_result(sel_request_id, args.timeout)

            if not sel_result:
                last_sel_error = {"error": "Timeout waiting for selector click result", "selector": selector}
                continue

            if sel_result.get("result", {}).get("ok", False):
                sel_success = True
                break

            last_sel_error = sel_result

        if sel_success:
            print(json.dumps({"ok": True, "phase": "complete", "cellIndex": idx, "note": "Cell deleted via selector", "selector": selector}, ensure_ascii=False))
            return resolved_tab_id

        # Fallback: send two 'd' keys using send_key (robust single-key path)
        for i, key in enumerate(["d", "d"], start=1):
            key_request_id = str(uuid.uuid4())
            key_cmd = _build_send_key_command(key_request_id, resolved_tab_id, key, args.url)
            _queue_command(key_cmd)
            key_result = _wait_for_request_result(key_request_id, args.timeout)

            if not key_result:
                print(json.dumps({"ok": False, "phase": "send_key_dd", "error": f"Timeout waiting for key {i} ('{key}') result"}, ensure_ascii=False))
                return resolved_tab_id

            if not key_result.get("result", {}).get("ok", False):
                print(json.dumps({"ok": False, "phase": "send_key_dd", "error": f"key {i} ('{key}') failed", "details": key_result}, ensure_ascii=False))
                return resolved_tab_id

            time.sleep(0.06)

        print(json.dumps({"ok": True, "phase": "complete", "cellIndex": idx, "note": "Cell deleted via dd keys (fallback)", "last_selector_error": last_sel_error}, ensure_ascii=False))
        return resolved_tab_id

    if args.index is not None:
        _process_delete(args.index, args.tab_id)
        return

    # Continuous loop
    current_tab_id = args.tab_id
    try:
        while True:
            raw_value = input("\nEnter cell index to delete (or Ctrl+C to quit): ").strip()
            if not raw_value:
                continue
            try:
                idx = int(raw_value)
                current_tab_id = _process_delete(idx, current_tab_id)
            except ValueError:
                print("Please enter a valid integer index.")
    except KeyboardInterrupt:
        print("\nExiting.")

if __name__ == "__main__":
    main()
