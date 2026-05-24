"""Insert a new cell above a given index and convert it to Markdown.

Flow:
- Select the target cell by index (click without running).
- Insert a new cell "above" the target using the host insert action.
- Click inside the newly inserted cell.
- Send Escape once to ensure command mode.
- Send `m` once to convert the new cell to Markdown.

This file follows the project's robust JSONL queueing pattern (bot_commands.jsonl / bot_results.jsonl)
and sends the `m` key itself after inserting the cell.
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
    seen = set()
    while time.time() < deadline:
        for event in _read_all_jsonl(BOT_RESULTS_PATH):
            eid = id(event)
            if eid in seen:
                continue
            if event.get("requestId") == request_id:
                seen.add(eid)
                return event
        time.sleep(0.5)
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


def _build_insert_command(
    request_id: str,
    tab_id: int | None,
    direction: str,
    url: str,
):
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
    parser = argparse.ArgumentParser(description="Insert a markdown cell above an index and convert it to markdown")
    parser.add_argument("index", type=int, help="Cell index to insert above (0-based)")
    parser.add_argument("--tab-id", type=int, default=None, help="Optional tab id")
    parser.add_argument("--url", default="https://www.kaggle.com/code/codekey/qwen2-5-coder-7b-instruct/edit", help="Optional notebook URL")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout in seconds for each step")
    args = parser.parse_args()

    if args.index < 0:
        print(json.dumps({"ok": False, "error": "index must be >= 0"}, ensure_ascii=False))
        return

    # 1) Select the target cell
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

    print(json.dumps({"ok": True, "phase": "cell_selected", "cellIndex": args.index, "tabId": resolved_tab_id}, ensure_ascii=False))

    # 2) Insert above
    print(json.dumps({"ok": True, "phase": "inserting_cell_above"}, ensure_ascii=False))
    insert_request_id = str(uuid.uuid4())
    insert_cmd = _build_insert_command(insert_request_id, resolved_tab_id, "above", args.url)
    _queue_command(insert_cmd)
    insert_result = _wait_for_request_result(insert_request_id, args.timeout)

    if insert_result is None:
        print(json.dumps({"ok": False, "phase": "insert_cell", "error": "Timeout waiting for insert result"}, ensure_ascii=False))
        return

    inner_insert = insert_result.get("result", {})
    if not inner_insert.get("ok", False):
        print(json.dumps({"ok": False, "phase": "insert_cell", "error": "Insert failed", "details": insert_result}, ensure_ascii=False))
        return

    # 3) Click inside the newly inserted cell (same index after inserting above)
    time.sleep(0.5)
    print(json.dumps({"ok": True, "phase": "selecting_new_cell", "cellIndex": args.index}, ensure_ascii=False))
    new_cell_click_request_id = str(uuid.uuid4())
    new_cell_click_cmd = _build_click_command(new_cell_click_request_id, resolved_tab_id, args.index, args.url)
    _queue_command(new_cell_click_cmd)
    new_cell_click_result = _wait_for_request_result(new_cell_click_request_id, args.timeout)

    if new_cell_click_result is None:
        print(json.dumps({"ok": False, "phase": "new_cell_selection", "error": "Timeout waiting for new cell selection"}, ensure_ascii=False))
        return

    if not new_cell_click_result.get("result", {}).get("ok", False):
        print(json.dumps({"ok": False, "phase": "new_cell_selection", "error": "New cell selection failed", "details": new_cell_click_result}, ensure_ascii=False))
        return

    # 4) Send Escape once to force command mode
    time.sleep(0.2)
    print(json.dumps({"ok": True, "phase": "sending_escape_key"}, ensure_ascii=False))
    escape_request_id = str(uuid.uuid4())
    escape_cmd = _build_send_key_command(escape_request_id, resolved_tab_id, "Escape", args.url)
    _queue_command(escape_cmd)
    escape_result = _wait_for_request_result(escape_request_id, args.timeout)

    if escape_result is None:
        print(json.dumps({"ok": False, "phase": "send_key_escape", "error": "Timeout waiting for Escape key result"}, ensure_ascii=False))
        return

    if not escape_result.get("result", {}).get("ok", False):
        print(json.dumps({"ok": False, "phase": "send_key_escape", "error": "Escape key failed", "details": escape_result}, ensure_ascii=False))
        return

    escape_tag = str(escape_result.get("result", {}).get("tagName", "")).upper()
    if escape_tag == "IFRAME":
        print(json.dumps({
            "ok": False,
            "phase": "send_key_escape",
            "error": "Escape key landed on IFRAME (false-positive dispatch). Reload extension once so updated frame routing takes effect.",
            "details": escape_result,
        }, ensure_ascii=False))
        return

    # 5) Send m once in command mode
    time.sleep(0.5)

    print(json.dumps({"ok": True, "phase": "sending_m_key"}, ensure_ascii=False))
    m_request_id = str(uuid.uuid4())
    m_cmd = _build_send_key_command(m_request_id, resolved_tab_id, "m", args.url)
    _queue_command(m_cmd)
    m_result = _wait_for_request_result(m_request_id, args.timeout)

    if m_result is None:
        print(json.dumps({"ok": False, "phase": "send_key_m", "error": "Timeout waiting for m key result"}, ensure_ascii=False))
        return

    if not m_result.get("result", {}).get("ok", False):
        print(json.dumps({"ok": False, "phase": "send_key_m", "error": "m key failed", "details": m_result}, ensure_ascii=False))
        return

    m_tag = str(m_result.get("result", {}).get("tagName", "")).upper()
    if m_tag == "IFRAME":
        print(json.dumps({
            "ok": False,
            "phase": "send_key_m",
            "error": "m key landed on IFRAME (false-positive dispatch). Reload extension once so updated frame routing takes effect.",
            "details": m_result,
        }, ensure_ascii=False))
        return

    print(json.dumps({"ok": True, "phase": "complete", "cellIndex": args.index, "tabId": resolved_tab_id, "note": "Inserted above, focused new cell, entered command mode, and converted to markdown"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
