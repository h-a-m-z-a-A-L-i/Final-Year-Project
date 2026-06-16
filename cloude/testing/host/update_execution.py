"""
update_execution.py  —  Minimal native host.
Receives NOTEBOOK_DATA from the extension and updates execution_order + execution_title in the JSON file.
Touches nothing else.
"""

import json, struct, sys, hashlib
from pathlib import Path
from datetime import datetime, timezone

SCRAPED_DIR = Path(__file__).parent / "data" / "notebooks"
SCRAPED_DIR.mkdir(parents=True, exist_ok=True)


# ── Native Messaging I/O ─────────────────────────────────────────────────────
def read_msg():
    raw = sys.stdin.buffer.read(4)
    if not raw or len(raw) < 4:
        return None
    length = struct.unpack("<I", raw)[0]
    payload = sys.stdin.buffer.read(length)
    if len(payload) < length:
        return None
    return json.loads(payload.decode("utf-8"))


def send_msg(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
    sys.stdout.buffer.flush()


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def safe_filename(url: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in url).strip("_")[:200] + ".json"


def execution_title(order) -> str:
    if order is None:
        return "Cell is not executed yet"
    t = datetime.now().strftime("%I:%M%p").lstrip("0").lower()
    return f"Cell executed at {t}"


# ── Core update logic ─────────────────────────────────────────────────────────
def update_json(tab_url: str, raw_cells: list, title: str):
    path = SCRAPED_DIR / safe_filename(tab_url)

    # Load existing data or start fresh
    existing: dict = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing_by_index = {
        str(c["index"]): c
        for c in existing.get("cells", [])
        if isinstance(c, dict) and c.get("index") is not None
    }

    updated_cells = []
    changed = False

    for i, cell in enumerate(raw_cells):
        if cell.get("type") != "code":
            continue

        idx = i + 1
        source = str(cell.get("source") or "")
        output = str(cell.get("output") or "")
        order = cell.get("execution_order")   # int or None
        status = str(cell.get("execution_status") or "idle")

        # Determine the execution title to save
        prev = existing_by_index.get(str(idx), {})
        prev_order = prev.get("execution_order")
        prev_title = prev.get("execution_title", "Cell is not executed yet")

        if order is not None and status in ("executed", "running", "queued"):
            # Extension reported a real execution order — use it
            new_order = order
            new_title = execution_title(order)
        elif prev_order is not None:
            # Keep what we already have
            new_order = prev_order
            new_title = prev_title
        else:
            new_order = None
            new_title = "Cell is not executed yet"

        # Detect any change
        if (
            str(prev.get("input", "")) != source
            or str(prev.get("output", "")) != output
            or str(prev_order) != str(new_order)
            or prev_title != new_title
        ):
            changed = True

        updated_cells.append({
            "index": idx,
            "input": source,
            "output": output,
            "execution_order": new_order,
            "execution_title": new_title,
        })

    if not changed and updated_cells:
        log(f"No change for {tab_url}")
        return

    data = {
        "tabUrl": tab_url,
        "title": title,
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "cells": updated_cells,
    }

    # Atomic write
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    log(f"Updated {path.name}  cells={len(updated_cells)}")


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    log("update_execution host started")
    while True:
        msg = read_msg()
        if msg is None:
            log("stdin closed")
            break

        if msg.get("type") != "NOTEBOOK_DATA":
            send_msg({"ok": True})
            continue

        tab_url = str(msg.get("tabUrl") or "unknown")
        raw_cells = msg.get("cells", [])
        title = str(msg.get("title") or "notebook")

        try:
            update_json(tab_url, raw_cells, title)
            send_msg({"ok": True})
        except Exception as e:
            log(f"ERROR: {e}")
            send_msg({"ok": False, "error": str(e)})


if __name__ == "__main__":
    main()
