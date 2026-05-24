"""Isolated copy of insert_cell.py placed under testing/host/tools.

Adjusted ROOT to point to the parent testing/host directory so metadata
paths remain the same as the original scripts.
"""
import argparse
import json
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_META = ROOT / "data" / "meta"
DATA_NOTEBOOKS = ROOT / "data" / "notebooks"
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


def _build_insert_command(request_id: str, tab_id: int | None, direction: str, url: str):
    cmd = {
        "action": "insert_cell",
        "requestId": request_id,
        "direction": direction,
    }
    if tab_id is not None:
        cmd["tabId"] = tab_id
    if url:
        cmd["url"] = url
    return cmd


def main():
    parser = argparse.ArgumentParser(description="Insert a cell above or below a target cell")
    parser.add_argument("index", type=int, help="Cell index to insert next to (0-based)")
    parser.add_argument("--tab-id", type=int, default=None, help="Optional tab id")
    parser.add_argument("--url", default="https://www.kaggle.com/code/codekey/qwen2-5-coder-7b-instruct/edit", help="Optional notebook URL (for metadata polling)")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout in seconds")
    args = parser.parse_args()

    if args.index < 0:
        print(json.dumps({"ok": False, "error": "index must be >= 0"}, ensure_ascii=False))
        return

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

    direction = "above" if choice == "a" else "below"

    print(json.dumps({
        "ok": True,
        "phase": "inserting_cell",
        "direction": direction,
    }, ensure_ascii=False))

    insert_request_id = str(uuid.uuid4())
    insert_cmd = _build_insert_command(insert_request_id, resolved_tab_id, direction, args.url)

    _queue_command(insert_cmd)
    insert_result = _wait_for_request_result(insert_request_id, args.timeout)

    if not insert_result:
        print(json.dumps({
            "ok": False,
            "phase": "insert_cell",
            "error": "Timeout waiting for insert result"
        }, ensure_ascii=False))
        return

    inner_insert_result = insert_result.get("result", {})
    if not inner_insert_result.get("ok", False):
        print(json.dumps({
            "ok": False,
            "phase": "insert_cell",
            "error": "Insert failed",
            "details": insert_result
        }, ensure_ascii=False))
        return

    print(json.dumps({
        "ok": True,
        "phase": "insert_requested",
        "direction": direction
    }, ensure_ascii=False))
    print(json.dumps({
        "ok": True,
        "phase": "complete",
        "direction": direction,
        "note": "Single insert action completed",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
