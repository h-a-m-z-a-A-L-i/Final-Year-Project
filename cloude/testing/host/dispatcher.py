import json
import queue
import struct
import sys
import threading
import time
from pathlib import Path

try:
    from . import jsonl_queue
except Exception:
    jsonl_queue = None

from .config import BOT_COMMANDS_PATH, BOT_RESULTS_PATH, _SEND_LOCK
from .bot_command import execute_bot_command_sync, complete_bot_result

_INCOMING_QUEUE: queue.Queue = queue.Queue()
_READER_STARTED = False

_BOT_RESULT_TYPES = frozenset({
    "CLICK_CELL_RESULT", "CLICK_CELL_ERROR",
    "CLICK_CELL_BY_INDEX_RESULT", "CLICK_CELL_BY_INDEX_ERROR",
    "CLICK_SELECTOR_RESULT", "CLICK_SELECTOR_ERROR",
    "SELECT_CELL_RESULT", "SELECT_CELL_ERROR",
    "SELECT_CELL_BY_INDEX_RESULT", "SELECT_CELL_BY_INDEX_ERROR",
    "RUN_CELL_RESULT", "RUN_CELL_ERROR",
    "RUN_CELL_BY_INDEX_RESULT", "RUN_CELL_BY_INDEX_ERROR",
    "INSERT_CELL_RESULT", "INSERT_CELL_ERROR",
    "SET_CELL_CONTENT_RESULT", "SET_CELL_CONTENT_ERROR",
    "SEND_KEY_RESULT", "SEND_KEY_ERROR",
    "DELETE_CELL_RESULT", "DELETE_CELL_ERROR",
    "CREATING_MARKDOWN_RESULT", "CREATING_MARKDOWN_ERROR",
    "CREATING_MARKDOWN_BY_INDEX_RESULT", "CREATING_MARKDOWN_BY_INDEX_ERROR",
    "SEND_KEYS_RESULT", "SEND_KEYS_ERROR",
})


def _handle_bot_command(cmd: dict) -> dict:
    """Execute a bot command and return a normalized bot_results.jsonl event."""
    timeout = float(cmd.get("timeout", 12.0))
    return execute_bot_command_sync(cmd, timeout=timeout)


def read_msg():
    raw = sys.stdin.buffer.read(4)
    if not raw:
        return None
    if len(raw) < 4:
        raise ValueError("Incomplete native message length")
    length = struct.unpack("<I", raw)[0]
    payload = sys.stdin.buffer.read(length)
    if len(payload) < length:
        raise ValueError("Incomplete native message payload")
    if not payload:
        return {}
    return json.loads(payload)


def send_msg(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    with _SEND_LOCK:
        sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
        sys.stdout.buffer.flush()


def _handle_bot_result_immediate(msg: dict) -> None:
    """Wake pending execute_bot_command_sync waiters while the main loop is busy."""
    try:
        record = complete_bot_result(msg)
        record["diagnostics"] = msg.get("diagnostics")
        if msg.get("tunnel"):
            record["tunnel"] = msg.get("tunnel")
        _append_jsonl(BOT_RESULTS_PATH, record)
        print(
            f"Bot result {msg.get('type')} ok={record.get('ok')} tabId={msg.get('tabId')} "
            f"cellIndex={msg.get('cellIndex')} requestId={msg.get('requestId')}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"Bot result dispatch failed: {exc}", file=sys.stderr)


def _start_stdin_reader() -> None:
    """Read native messages on a background thread so bot results are not starved."""
    global _READER_STARTED
    if _READER_STARTED:
        return
    _READER_STARTED = True

    def _reader() -> None:
        while True:
            try:
                msg = read_msg()
                if msg is None:
                    _INCOMING_QUEUE.put(None)
                    break
                m_type = msg.get("type")
                if m_type in _BOT_RESULT_TYPES:
                    _handle_bot_result_immediate(msg)
                    continue
                _INCOMING_QUEUE.put(msg)
            except Exception as exc:
                print(f"stdin reader error: {exc}", file=sys.stderr)
                time.sleep(0.1)

    threading.Thread(target=_reader, daemon=True).start()


def get_next_message():
    """Next extension message for the main loop (bot results are handled in the reader)."""
    return _INCOMING_QUEUE.get()


def _append_jsonl(path: Path, payload: dict):
    try:
        if jsonl_queue:
            jsonl_queue.append_jsonl(path, payload)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        try:
            print(f"Failed writing {path.name}: {e}", file=sys.stderr)
        except Exception:
            pass


def _start_bot_command_watcher():
    def _worker():
        offset = 0
        try:
            BOT_COMMANDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            BOT_COMMANDS_PATH.touch(exist_ok=True)
            offset = BOT_COMMANDS_PATH.stat().st_size
            print("Bot watcher started (data/meta/bot_commands.jsonl)", file=sys.stderr)
        except Exception as e:
            print(f"Bot watcher init failed: {e}", file=sys.stderr)
            return

        while True:
            try:
                if not BOT_COMMANDS_PATH.exists():
                    time.sleep(0.01)
                    continue
                size = BOT_COMMANDS_PATH.stat().st_size
                if size < offset:
                    offset = 0
                if size == offset:
                    time.sleep(0.01)
                    continue

                cmds = []
                if jsonl_queue:
                    try:
                        new_offset, events = jsonl_queue.tail_from(BOT_COMMANDS_PATH, offset)
                        offset = new_offset
                        cmds = events
                    except Exception:
                        pass

                if not cmds:
                    with BOT_COMMANDS_PATH.open("r", encoding="utf-8") as f:
                        f.seek(offset)
                        chunk = f.read()
                        offset = f.tell()
                    for raw in chunk.splitlines():
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            cmds.append(json.loads(line))
                        except Exception:
                            _append_jsonl(BOT_RESULTS_PATH, {
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "ok": False,
                                "error": "Invalid JSON command",
                                "raw": line,
                            })
                            continue

                for cmd in cmds:
                    try:
                        result_event = _handle_bot_command(cmd)
                        _append_jsonl(BOT_RESULTS_PATH, result_event)
                        print(
                            f"Bot command {str(cmd.get('action') or cmd.get('type') or '').strip().lower()} "
                            f"requestId={cmd.get('requestId')} ok={result_event.get('ok')}",
                            file=sys.stderr,
                        )
                    except Exception as exc:
                        _append_jsonl(BOT_RESULTS_PATH, {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "ok": False,
                            "error": f"dispatch_error:{exc}",
                            "requestId": cmd.get("requestId"),
                            "result": {"ok": False, "error": str(exc)},
                        })

            except Exception as e:
                print(f"Bot watcher loop error: {e}", file=sys.stderr)
                time.sleep(0.05)

    threading.Thread(target=_worker, daemon=True).start()


def start_host_io() -> None:
    """Start stdin reader and bot command watcher (call once during host init)."""
    _start_stdin_reader()
    _start_bot_command_watcher()
