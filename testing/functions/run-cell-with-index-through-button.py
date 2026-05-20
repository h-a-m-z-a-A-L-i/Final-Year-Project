"""Simple cell click bot client.

Writes a command into data/meta/bot_commands.jsonl.
host.py consumes it and dispatches CLICK_CELL_BY_INDEX or CLICK_SELECTOR to the extension.
"""
import argparse
import json
import time
import uuid
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
DATA_META = ROOT / "data" / "meta"
BOT_COMMANDS_PATH = DATA_META / "bot_commands.jsonl"
BOT_RESULTS_PATH = DATA_META / "bot_results.jsonl"

# Centralized JSONL helpers
HOST_PKG = ROOT.parent / "host"
if str(HOST_PKG) not in sys.path:
    sys.path.insert(0, str(HOST_PKG))
from jsonl_queue import append_jsonl, tail_from, wait_for_request_result
DEFAULT_RUN_BUTTON_SELECTOR = (
    "button[data-test-id='run-cell'], "
    "button[aria-label*='Run Cell'], "
    "button[title*='Run Cell'], "
    "button[aria-label*='Run'], "
    "button[title*='Run'], "
    "button[aria-label*='Execute'], "
    "button[title*='Execute']"
)


def _append_jsonl(path: Path, payload: dict):
    append_jsonl(path, payload)


def _read_results_since(path: Path, since_size: int):
    return tail_from(path, since_size)[1]


def _wait_for_request_result(request_id: str, before_size: int, timeout_seconds: float):
    return wait_for_request_result(request_id, BOT_RESULTS_PATH, timeout_seconds, before_size)


def _queue_command(cmd: dict):
    append_jsonl(BOT_COMMANDS_PATH, cmd)


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
    parser.add_argument("--run-selector", default=DEFAULT_RUN_BUTTON_SELECTOR, help="CSS selector for the run button to click after the cell is selected")
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

    if cell_index is None and not args.selector.strip():
        raw_value = input("Cell index: ").strip()
        if not raw_value:
            print(json.dumps({"ok": False, "error": "No cell index provided"}, ensure_ascii=False))
            return
        try:
            cell_index = int(raw_value)
        except Exception:
            print(json.dumps({"ok": False, "error": "Cell index must be an integer"}, ensure_ascii=False))
            return

    if cell_index is None and not args.selector.strip():
        print(json.dumps({"ok": False, "error": "cell_index is required unless --selector is provided"}, ensure_ascii=False))
        return

    request_id = str(uuid.uuid4())
    before = BOT_RESULTS_PATH.stat().st_size if BOT_RESULTS_PATH.exists() else 0

    if args.selector.strip():
        if tab_id is None:
            print(json.dumps({"ok": False, "error": "--tab-id is required when using --selector"}, ensure_ascii=False))
            return
        cmd = _build_selector_command(request_id, tab_id, args.selector.strip(), args.url)
    else:
        if cell_index < 0:
            print(json.dumps({"ok": False, "error": "cell_index must be >= 0"}, ensure_ascii=False))
            return
        cmd = _build_click_command(request_id, tab_id, cell_index, args.url)

    _queue_command(cmd)
    result = _wait_for_request_result(request_id, before, args.timeout)
    if result is None:
        print(json.dumps({
            "ok": False,
            "requestId": request_id,
            "error": "Timed out waiting for bot result. Ensure host.py is running and extension is connected.",
        }, ensure_ascii=False))
        return

    # The host appends the extension's result under a "result" key in the BOT_RESULTS_PATH entry
    inner_result = result.get("result", {})
    if not inner_result.get("ok", False) or args.selector.strip() or not args.run_selector:
        print(json.dumps(result, ensure_ascii=False))
        return

    resolved_tab_id = inner_result.get("tabId") if isinstance(inner_result.get("tabId"), int) else result.get("tabId")
    if resolved_tab_id is None:
        resolved_tab_id = tab_id
        
    if resolved_tab_id is None:
        print(json.dumps({
            "ok": False,
            "requestId": request_id,
            "error": "Cell clicked, but no tab id was available for the run step.",
        }, ensure_ascii=False))
        return

    print(json.dumps({"ok": True, "phase": "cell_selected", "tabId": resolved_tab_id, "cellIndex": cell_index}, ensure_ascii=False))
    time.sleep(0.5)

    run_request_id = str(uuid.uuid4())
    run_before = BOT_RESULTS_PATH.stat().st_size if BOT_RESULTS_PATH.exists() else 0
    
    # Use a more specific selector if we have a cell index, falling back to the global one
    if cell_index is not None:
        specific_run_selector = f'{args.run_selector.strip()}, [data-windowed-list-index="{cell_index}"] button[aria-label*="Run"]'
        run_cmd = _build_selector_command(run_request_id, resolved_tab_id, specific_run_selector, args.url)
    else:
        run_cmd = _build_selector_command(run_request_id, resolved_tab_id, args.run_selector.strip(), args.url)
        
    _queue_command(run_cmd)
    run_result = _wait_for_request_result(run_request_id, run_before, args.timeout)
    if run_result is None:
        print(json.dumps({
            "ok": False,
            "requestId": run_request_id,
            "error": "Clicked cell, but timed out waiting for run-button result.",
        }, ensure_ascii=False))
        return

    run_inner_result = run_result.get("result", {})
    print(json.dumps({
        "ok": bool(run_inner_result.get("ok", False)),
        "cellClick": result,
        "runClick": run_result,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
