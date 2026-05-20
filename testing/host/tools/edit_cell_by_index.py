"""Click any cell by index and put it into edit mode.

Behavior:
- Click the target cell wrapper by index.
- If the cell is a code cell (class contains `jp-CodeCell`), click its input area.
- If the cell is a markdown cell (class contains `jp-MarkdownCell`), double-click the cell
  to enter edit mode, then click the editor area with class `cm-content cm-lineWrapping`.

This follows the project's JSONL queueing pattern used by other host tools.
"""
import argparse
import json
import time
import uuid
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
DATA_META = ROOT / "data" / "meta"
BOT_COMMANDS_PATH = DATA_META / "bot_commands.jsonl"
BOT_RESULTS_PATH = DATA_META / "bot_results.jsonl"

# Centralized JSONL helpers
HOST_PKG = ROOT
if str(HOST_PKG) not in sys.path:
    sys.path.insert(0, str(HOST_PKG))
from jsonl_queue import append_jsonl, tail_from, wait_for_request_result


def _wait_for_request_result(request_id: str, timeout_seconds: float):
    before = BOT_RESULTS_PATH.stat().st_size if BOT_RESULTS_PATH.exists() else 0
    return wait_for_request_result(request_id, BOT_RESULTS_PATH, timeout_seconds, before)


def _queue_command(cmd: dict):
    append_jsonl(BOT_COMMANDS_PATH, cmd)


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


def main():
    parser = argparse.ArgumentParser(description="Click and edit a cell by index")
    parser.add_argument("index", type=int, help="Cell index to edit (0-based)")
    parser.add_argument("--tab-id", type=int, default=None, help="Optional tab id")
    parser.add_argument("--url", default="", help="Optional notebook URL")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout in seconds for each step")
    args = parser.parse_args()

    if args.index < 0:
        print(json.dumps({"ok": False, "error": "index must be >= 0"}, ensure_ascii=False))
        return

    # 1) Click the cell wrapper
    print(json.dumps({"ok": True, "phase": "selecting_cell", "cellIndex": args.index}, ensure_ascii=False))
    click_request_id = str(uuid.uuid4())
    click_cmd = _build_click_command(click_request_id, args.tab_id, args.index, args.url)
    _queue_command(click_cmd)
    click_result = _wait_for_request_result(click_request_id, args.timeout)

    if click_result is None:
        print(json.dumps({"ok": False, "phase": "cell_selection", "error": "Timeout waiting for cell selection"}, ensure_ascii=False))
        return

    inner = click_result.get("result", {})
    if not inner.get("ok", False):
        print(json.dumps({"ok": False, "phase": "cell_selection", "error": "Cell selection failed", "details": click_result}, ensure_ascii=False))
        return

    resolved_tab_id = args.tab_id
    if isinstance(inner.get("tabId"), int):
        resolved_tab_id = inner["tabId"]
    elif isinstance(click_result.get("tabId"), int):
        resolved_tab_id = click_result["tabId"]

    if resolved_tab_id is None:
        print(json.dumps({"ok": False, "phase": "cell_selection", "error": "Could not resolve tab id"}, ensure_ascii=False))
        return

    clicked_desc = str(inner.get("clicked") or inner.get("clickedElement") or "")
    cell_type = None
    if "jp-CodeCell" in clicked_desc or "codecell" in clicked_desc.lower():
        cell_type = "code"
    elif "jp-MarkdownCell" in clicked_desc or "markdowncell" in clicked_desc.lower():
        cell_type = "markdown"

    print(json.dumps({"ok": True, "phase": "cell_selected", "cellIndex": args.index, "tabId": resolved_tab_id, "detected": cell_type or "unknown"}, ensure_ascii=False))

    # If unknown, try quick probes to detect cell type
    if cell_type is None:
        # Try code editor selector probe
        probe_sel = f'[data-windowed-list-index="{args.index}"] .jp-InputArea-editor'
        probe_id = str(uuid.uuid4())
        probe_cmd = _build_selector_command(probe_id, resolved_tab_id, probe_sel, args.url)
        _queue_command(probe_cmd)
        probe_res = _wait_for_request_result(probe_id, 1.0)
        if probe_res and probe_res.get("result", {}).get("ok", False):
            cell_type = "code"
        else:
            cell_type = "markdown"


    # For code cells: pressing Enter will reveal the input area reliably
    if cell_type == "code":
        # 1) Send Enter to put the cell into edit mode (brings up the input area)
        enter_id = str(uuid.uuid4())
        enter_cmd = _build_send_key_command(enter_id, resolved_tab_id, "Enter", args.url)
        _queue_command(enter_cmd)
        enter_res = _wait_for_request_result(enter_id, args.timeout)

        if not (enter_res and enter_res.get("result", {}).get("ok", False)):
            print(json.dumps({"ok": False, "phase": "send_enter", "error": "Timeout or failure sending Enter key", "details": enter_res}, ensure_ascii=False))
            return

        # small delay to allow UI to reveal the input area
        time.sleep(0.06)

        # 2) Probe for known code editor selectors to confirm edit mode
        sels = [
            f'[data-windowed-list-index="{args.index}"] .jp-InputArea-editor',
            f'[data-windowed-list-index="{args.index}"] .cm-editor',
            f'[data-windowed-list-index="{args.index}"] [role="textbox"]',
        ]
        success = False
        last_res = None
        for sel in sels:
            sel_id = str(uuid.uuid4())
            sel_cmd = _build_selector_command(sel_id, resolved_tab_id, sel, args.url)
            _queue_command(sel_cmd)
            sel_res = _wait_for_request_result(sel_id, args.timeout)
            last_res = sel_res
            if sel_res and sel_res.get("result", {}).get("ok", False):
                success = True
                print(json.dumps({"ok": True, "phase": "entered_edit_mode", "cellType": "code", "selector": sel}, ensure_ascii=False))
                break
        if not success:
            print(json.dumps({"ok": False, "phase": "enter_edit_mode", "error": "Editor did not appear after Enter", "lastResult": last_res}, ensure_ascii=False))
            return

    else:  # markdown
        # Double-click the rendered markdown area to start editing, then click editor line
        dbl_sel = f'[data-windowed-list-index="{args.index}"]'
        first_id = str(uuid.uuid4())
        first_cmd = _build_selector_command(first_id, resolved_tab_id, dbl_sel, args.url)
        _queue_command(first_cmd)
        first_res = _wait_for_request_result(first_id, args.timeout)
        time.sleep(0.08)
        second_id = str(uuid.uuid4())
        second_cmd = _build_selector_command(second_id, resolved_tab_id, dbl_sel, args.url)
        _queue_command(second_cmd)
        second_res = _wait_for_request_result(second_id, args.timeout)

        if not (first_res and first_res.get("result", {}).get("ok", False)):
            print(json.dumps({"ok": False, "phase": "double_click", "error": "First click failed", "details": first_res}, ensure_ascii=False))
            return
        if not (second_res and second_res.get("result", {}).get("ok", False)):
            print(json.dumps({"ok": False, "phase": "double_click", "error": "Second click failed", "details": second_res}, ensure_ascii=False))
            return

        # Now click the editor inner line area
        editor_sel = f'[data-windowed-list-index="{args.index}"] .cm-content.cm-lineWrapping'
        editor_id = str(uuid.uuid4())
        editor_cmd = _build_selector_command(editor_id, resolved_tab_id, editor_sel, args.url)
        _queue_command(editor_cmd)
        editor_res = _wait_for_request_result(editor_id, args.timeout)

        if not (editor_res and editor_res.get("result", {}).get("ok", False)):
            print(json.dumps({"ok": False, "phase": "enter_markdown_edit", "error": "Failed to click markdown editor area", "details": editor_res}, ensure_ascii=False))
            return

        print(json.dumps({"ok": True, "phase": "entered_edit_mode", "cellType": "markdown", "selector": editor_sel}, ensure_ascii=False))

    print(json.dumps({"ok": True, "phase": "complete", "cellIndex": args.index, "cellType": cell_type}, ensure_ascii=False))


if __name__ == "__main__":
    main()
