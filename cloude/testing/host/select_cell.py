"""Notebook cell selector.

Selects a notebook cell by index without running it.
"""
import argparse
import json
import uuid

from notebook_bot_client import DEFAULT_TAB_ID, queue_command, wait_for_request_result


def _build_select_command(request_id: str, tab_id: int | None, cell_index: int, url: str):
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


def main():
    parser = argparse.ArgumentParser(description="Select a notebook cell by index without executing it")
    parser.add_argument("index", type=int, help="Cell index to select (0-based)")
    parser.add_argument("--tab-id", type=int, default=DEFAULT_TAB_ID, help="Optional tab id")
    parser.add_argument("--url", default="", help="Optional notebook URL")
    parser.add_argument("--timeout", type=float, default=8.0, help="Seconds to wait for a response")
    args = parser.parse_args()

    if args.index < 0:
        print(json.dumps({"ok": False, "error": "index must be >= 0"}, ensure_ascii=False))
        return

    request_id = str(uuid.uuid4())
    queue_command(_build_select_command(request_id, args.tab_id, args.index, args.url))
    result = wait_for_request_result(request_id, args.timeout)

    if result is None:
        print(json.dumps({"ok": False, "requestId": request_id, "error": "Timed out waiting for selection result"}, ensure_ascii=False))
        return

    inner_result = result.get("result", {})
    if not inner_result.get("ok", False):
        print(json.dumps(result, ensure_ascii=False))
        return

    print(json.dumps({
        "ok": True,
        "phase": "cell_selected",
        "cellIndex": args.index,
        "tabId": result.get("tabId") or args.tab_id,
    }, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
