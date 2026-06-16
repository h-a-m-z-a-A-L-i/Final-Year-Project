import json
import os
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DATABASE_DIR = Path(os.environ.get("KAGGLE_SCRAPER_DB_DIR", WORKSPACE_ROOT / "database"))
LOG_FILE = DATABASE_DIR / "native_host.log"
# Import persistence helpers from testing/host when available
try:
    import sys
    HOST_PKG = Path(__file__).resolve().parents[1] / "testing" / "host"
    if str(HOST_PKG) not in sys.path:
        sys.path.insert(0, str(HOST_PKG))
    import persistence
except Exception:
    persistence = None


def log_line(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {message}"

    print(line, file=sys.stderr, flush=True)

    try:
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    if len(raw_length) < 4:
        raise ValueError("native_message_incomplete_length")

    length = struct.unpack("<I", raw_length)[0]
    raw_payload = sys.stdin.buffer.read(length)
    if len(raw_payload) < length:
        raise ValueError("native_message_incomplete_payload")

    if not raw_payload:
        return {}

    return json.loads(raw_payload.decode("utf-8"))


def write_message(message):
    encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def safe_slug(value: str, max_length: int = 120) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "").strip("-._")
    if not slug:
        slug = "unknown"
    return slug[:max_length]


def notebook_slug(tab_url: str) -> str:
    try:
        parsed = urlparse(tab_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 4 and parts[0] == "code":
            return safe_slug(f"{parts[1]}_{parts[2]}")
        if parts:
            return safe_slug(parts[-1])
    except Exception:
        pass
    return "kaggle_notebook"


def save_payload(payload):
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)

    slug = notebook_slug(str(payload.get("tabUrl", "")))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_path = DATABASE_DIR / f"{slug}_{timestamp}.json"
    # Use atomic writer when available to avoid partial writes
    if persistence:
        persistence.atomic_write_json(output_path, payload)
    else:
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    return output_path


def summarize_payload(payload, output_path: Path) -> str:
    tab_id = payload.get("tabId", "?")
    cell_count = payload.get("cellCount", "?")
    tab_url = payload.get("tabUrl", "")
    flags = payload.get("payloadFlags", {})
    active_flags = [key for key, value in flags.items() if value]
    flags_text = ",".join(active_flags) if active_flags else "none"

    return (
        f"saved tabId={tab_id} cells={cell_count} flags={flags_text} "
        f"file={output_path.name} url={tab_url}"
    )


def handle_message(message):
    message_type = message.get("type")
    if message_type != "NOTEBOOK_SCRAPE":
        return {
            "ok": False,
            "error": f"unsupported_message_type:{message_type}"
        }

    try:
        output_path = save_payload(message)
        log_line(summarize_payload(message, output_path))
        return {
            "ok": True,
            "savedPath": str(output_path),
            "cellCount": message.get("cellCount", 0)
        }
    except Exception as exc:
        error_text = f"save_failed:{exc}"
        log_line(error_text)
        return {
            "ok": False,
            "error": error_text
        }


def main() -> int:
    log_line("native_host_started")

    while True:
        try:
            message = read_message()
            if message is None:
                log_line("native_host_stdin_closed")
                break

            response = handle_message(message)
            write_message(response)
        except Exception as exc:
            error_text = f"native_host_exception:{exc}"
            log_line(error_text)
            try:
                write_message({"ok": False, "error": error_text})
            except Exception:
                break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
