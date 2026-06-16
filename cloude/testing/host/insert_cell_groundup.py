"""Notebook cell inserter.

Workflow:
1. Select a cell by index.
2. Ask whether to insert above or below.
3. Insert the cell using notebook insert actions, not keyboard shortcuts.
4. Confirm the notebook metadata reflects the new cell count when possible.
"""
import argparse
import json
import time
import uuid

from notebook_bot_client import DEFAULT_TAB_ID, queue_command, read_notebook_cell_count, wait_for_request_result


def _build_select_command(request_id: str, tab_id: int | None, cell_index: int, url: str):
    cmd = {
        "action": "click",
        "requestId": request_id,
        "cellIndex": cell_index,
        "scrollIntoView": True,
    }
    # Use the legacy click action with runCell disabled so a running host
    # that hasn't been restarted yet will accept the command and not execute.
    cmd["runCell"] = False
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


def _prompt_direction():
    while True:
        try:
            raw_value = input("\nInsert (a)bove or (b)elow? [a/b]: ").strip().lower()
        except KeyboardInterrupt:
            print("\nAborted.")
            return None
        if raw_value in {"a", "b"}:
            return "above" if raw_value == "a" else "below"
        print("Invalid choice. Enter 'a' for above or 'b' for below.")


def main():
    parser = argparse.ArgumentParser(description="Insert a notebook cell above or below a target cell")
    parser.add_argument("index", type=int, help="Cell index to select before inserting")
    parser.add_argument("--tab-id", type=int, default=DEFAULT_TAB_ID, help="Optional tab id")
    parser.add_argument("--url", default="", help="Optional notebook URL")
    parser.add_argument("--timeout", type=float, default=8.0, help="Seconds to wait for a response")
    args = parser.parse_args()

    if args.index < 0:
        print(json.dumps({"ok": False, "error": "index must be >= 0"}, ensure_ascii=False))
        return

    print(json.dumps({"ok": True, "phase": "selecting_cell", "cellIndex": args.index}, ensure_ascii=False))
    select_request_id = str(uuid.uuid4())
    queue_command(_build_select_command(select_request_id, args.tab_id, args.index, args.url))
    select_result = wait_for_request_result(select_request_id, args.timeout)

    if select_result is None:
        print(json.dumps({"ok": False, "phase": "cell_selection", "error": "Timed out waiting for selection result"}, ensure_ascii=False))
        return

    inner_select_result = select_result.get("result", {})
    if not inner_select_result.get("ok", False):
        print(json.dumps({"ok": False, "phase": "cell_selection", "error": "Cell selection failed", "details": select_result}, ensure_ascii=False))
        return

    resolved_tab_id = args.tab_id
    if isinstance(select_result.get("tabId"), int):
        resolved_tab_id = select_result["tabId"]
    elif isinstance(inner_select_result.get("tabId"), int):
        resolved_tab_id = inner_select_result["tabId"]

    if resolved_tab_id is None:
        print(json.dumps({"ok": False, "phase": "cell_selection", "error": "Could not resolve tab ID"}, ensure_ascii=False))
        return

    print(json.dumps({"ok": True, "phase": "cell_selected", "cellIndex": args.index, "tabId": resolved_tab_id}, ensure_ascii=False))

    direction = _prompt_direction()
    if direction is None:
        return

    print(json.dumps({"ok": True, "phase": "inserting_cell", "direction": direction}, ensure_ascii=False))

    initial_count = read_notebook_cell_count(args.url) if args.url else None
    insert_request_id = str(uuid.uuid4())
    queue_command(_build_insert_command(insert_request_id, resolved_tab_id, direction, args.url))
    insert_result = wait_for_request_result(insert_request_id, args.timeout)

    if insert_result is None:
        print(json.dumps({"ok": False, "phase": "insert_cell", "error": "Timed out waiting for insert result"}, ensure_ascii=False))
        return

    inner_insert_result = insert_result.get("result", {})
    if not inner_insert_result.get("ok", False):
        print(json.dumps({"ok": False, "phase": "insert_cell", "error": "Insert failed", "details": insert_result}, ensure_ascii=False))
        return

    if initial_count is None:
        print(json.dumps({
            "ok": True,
            "phase": "complete",
            "direction": direction,
            "note": "Insert action completed; metadata confirmation skipped because no URL was provided",
        }, ensure_ascii=False))
        return

    deadline = time.time() + max(1.0, args.timeout * 0.5)
    current_count = initial_count
    while time.time() < deadline:
        current_count = read_notebook_cell_count(args.url)
        if current_count is not None and current_count > initial_count:
            break
        time.sleep(0.5)

    if current_count is not None and current_count > initial_count:
        print(json.dumps({
            "ok": True,
            "phase": "complete",
            "direction": direction,
            "previousCellCount": initial_count,
            "newCellCount": current_count,
        }, ensure_ascii=False))
    else:
        print(json.dumps({
            "ok": True,
            "phase": "complete",
            "direction": direction,
            "note": "Insert completed but metadata confirmation did not observe a cell-count change in time",
            "previousCellCount": initial_count,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
