import json
import struct
import sys
import threading
import time
from pathlib import Path
try:
    from . import jsonl_queue
except Exception:
    jsonl_queue = None

from .config import BOT_COMMANDS_PATH, BOT_RESULTS_PATH, _SEND_LOCK, _BOT_STATE_LOCK
from .config import _ACTIVE_STREAMS_LOCK


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


def _append_jsonl(path: Path, payload: dict):
    try:
        if jsonl_queue:
            jsonl_queue.append_jsonl(path, payload)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        # best-effort logging via stderr
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
                    time.sleep(0.25)
                    continue
                size = BOT_COMMANDS_PATH.stat().st_size
                if size < offset:
                    offset = 0
                if size == offset:
                    time.sleep(0.25)
                    continue

                # Tail new commands using jsonl_queue when available
                cmds = []
                if jsonl_queue:
                    try:
                        new_offset, events = jsonl_queue.tail_from(BOT_COMMANDS_PATH, offset)
                        offset = new_offset
                        cmds = events
                    except Exception:
                        # fallback to manual read
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

                # Dispatch simple actions locally by writing responses to BOT_RESULTS_PATH
                for cmd in cmds:
                    action = str(cmd.get("action") or cmd.get("type") or "").strip().lower()
                    # This watcher primarily records bad or unsupported actions; real dispatch occurs in host send_msg
                    if action not in {"click", "click_cell_by_index", "click_selector", "select_cell_by_index", "insert_cell", "send_key", "send_keys"}:
                        _append_jsonl(BOT_RESULTS_PATH, {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "ok": False,
                            "error": f"Unsupported action: {action or 'missing'}",
                            "requestId": cmd.get("requestId"),
                        })
                        continue

                # sleeping loop cadence
            except Exception as e:
                print(f"Bot watcher loop error: {e}", file=sys.stderr)
                time.sleep(0.25)

    threading.Thread(target=_worker, daemon=True).start()
