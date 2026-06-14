"""Queue browser commands via bot_commands.jsonl when not inside the native host process."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

try:
    from .config import BOT_COMMANDS_PATH, BOT_RESULTS_PATH
except Exception:
    from config import BOT_COMMANDS_PATH, BOT_RESULTS_PATH

_NATIVE_HOST_ENV = "NOTEBOOK_COPILOT_NATIVE_HOST"


def is_native_host_process() -> bool:
    return os.environ.get(_NATIVE_HOST_ENV) == "1"


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _results_since(path: Path, offset: int) -> list[dict]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as f:
        f.seek(max(0, offset))
        raw = f.read()
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def execute_bot_command_queued(cmd: dict, timeout: float = 12.0) -> dict:
    """
    Append command to bot_commands.jsonl and wait for matching bot_results.jsonl line.

    Fire-and-forget by default: queue/dispatch and return immediately unless waitForResult is set.
    """
    try:
        from .bot_command import _is_fire_and_forget
    except Exception:
        from bot_command import _is_fire_and_forget

    request_id = str(cmd.get("requestId") or uuid.uuid4())
    queued = dict(cmd)
    queued["requestId"] = request_id
    queued.setdefault("timeout", timeout)

    BOT_COMMANDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOT_COMMANDS_PATH.touch(exist_ok=True)
    BOT_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOT_RESULTS_PATH.touch(exist_ok=True)

    _append_jsonl(BOT_COMMANDS_PATH, queued)

    if _is_fire_and_forget(queued):
        action = str(queued.get("action") or "unknown").strip().lower()
        try:
            from .bot_command import _dom_index_from_cmd
            from .cell_index import dom_to_app
        except Exception:
            from bot_command import _dom_index_from_cmd
            from cell_index import dom_to_app
        dom_index = queued.get("dom_index")
        if dom_index is None:
            dom_index = _dom_index_from_cmd(queued, default_basis=queued.get("index_basis") or "app")
        app_index = queued.get("app_index")
        if app_index is None and isinstance(dom_index, int):
            app_index = dom_to_app(dom_index)
        return {
            "ok": True,
            "requestId": request_id,
            "type": action.upper(),
            "url": queued.get("url"),
            "result": {
                "ok": True,
                "dispatched": True,
                "phase": "dispatched",
                "domIndex": dom_index,
                "appIndex": app_index,
                "cellIndex": app_index,
            },
            "transport": "jsonl_queue",
        }

    results_offset = BOT_RESULTS_PATH.stat().st_size

    action = str(queued.get("action") or "").strip().lower()
    composite_actions = {
        "insert_and_edit_cell",
        "insert_code_below",
        "edit_and_run_cell",
        "edit_and_run",
        "creating_markdown_by_index",
        "creating_markdown",
        "insert_cell",
    }
    cushion = 2.5 if action in composite_actions else 0.75
    deadline = time.monotonic() + max(0.5, float(timeout)) + cushion
    while time.monotonic() < deadline:
        for event in _results_since(BOT_RESULTS_PATH, results_offset):
            if event.get("requestId") == request_id:
                return event
        time.sleep(0.01)

    return {
        "ok": False,
        "error": (
            "timeout waiting for bot result — is host.py running with the extension connected? "
            "Start: python testing/host/host.py (or your usual host launcher), open the notebook tab, reload extension."
        ),
        "requestId": request_id,
        "transport": "jsonl_queue",
    }


def execute_bot_command(cmd: dict, timeout: float = 12.0) -> dict:
    """Route to native stdin/stdout when inside host.py, else JSONL queue."""
    if is_native_host_process():
        try:
            from .bot_command import execute_bot_command_sync
        except Exception:
            from bot_command import execute_bot_command_sync
        return execute_bot_command_sync(cmd, timeout=timeout)
    return execute_bot_command_queued(cmd, timeout=timeout)
