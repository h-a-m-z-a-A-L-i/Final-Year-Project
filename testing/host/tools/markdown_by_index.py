"""Insert a new cell above a given index and convert it to Markdown.

Flow:
- Select the target cell by index (click without running).
- Insert a new cell "above" the target using the host insert action.
- Wait 0.5s, then send single `m` key to convert the new cell to Markdown.

This file follows the project's robust JSONL queueing pattern (bot_commands.jsonl / bot_results.jsonl)
and uses single-key `send_key` actions rather than bulk key sequences.
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
        time.sleep(0.15)
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
    to_markdown: bool = False,
    markdown_delay_ms: int | None = None,
):
    cmd = {
        "action": "insert_cell",
        "requestId": request_id,
        "direction": direction,
    }
    if to_markdown:
        cmd["toMarkdown"] = True
    if markdown_delay_ms is not None:
        cmd["markdownDelayMs"] = int(markdown_delay_ms)
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
    parser.add_argument("--url", default="", help="Optional notebook URL")
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

    # 2) Insert above and request markdown conversion (m key) after 0.5s in extension context
    print(json.dumps({"ok": True, "phase": "inserting_cell_above"}, ensure_ascii=False))
    insert_request_id = str(uuid.uuid4())
    insert_cmd = _build_insert_command(
        insert_request_id,
        resolved_tab_id,
        "above",
        args.url,
        to_markdown=True,
        markdown_delay_ms=500,
    )
    _queue_command(insert_cmd)
    insert_result = _wait_for_request_result(insert_request_id, args.timeout)

    if insert_result is None:
        print(json.dumps({"ok": False, "phase": "insert_cell", "error": "Timeout waiting for insert result"}, ensure_ascii=False))
        return

    inner_insert = insert_result.get("result", {})
    if not inner_insert.get("ok", False):
        print(json.dumps({"ok": False, "phase": "insert_cell", "error": "Insert failed", "details": insert_result}, ensure_ascii=False))
        return

    print(json.dumps({"ok": True, "phase": "complete", "cellIndex": args.index, "note": "Inserted above and converted to markdown"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
