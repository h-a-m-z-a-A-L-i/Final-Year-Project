#!/usr/bin/env python3
"""
step1_backend.py  –  Native Messaging host for normal-chrome extension.

Chrome launches this script in the background (via run_host.generated.cmd).
Do NOT run it manually — Chrome owns its stdin/stdout.

All human-readable output is written to:
  database/native_host.log   ← watch this file to see live output

How to watch live (PowerShell):
  Get-Content -Path "D:\\FYP\\normal-chrome\\database\\native_host.log" -Wait -Tail 30

What this script does:
  1. Reads 4-byte-length-prefixed JSON messages from Chrome (Native Messaging protocol).
  2. Routes each message type to the correct handler.
  3. Sends a JSON acknowledgement back to Chrome for every message.
  4. Logs everything to the log file so you can observe it externally.
"""

import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(__file__).resolve().parent
DATABASE_DIR   = WORKSPACE_ROOT / "database"
LOG_FILE       = DATABASE_DIR / "native_host.log"

# ── Logging ──────────────────────────────────────────────────────────────────
def log(message: str) -> None:
    """Write a timestamped line to stderr AND the log file."""
    line = f"[{datetime.now(timezone.utc).isoformat()}] {message}"
    print(line, file=sys.stderr, flush=True)
    try:
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Native Messaging I/O ─────────────────────────────────────────────────────
def read_message() -> dict | None:
    """Read one 4-byte-prefixed JSON message from Chrome. Returns None on EOF."""
    raw_len = sys.stdin.buffer.read(4)
    if not raw_len:
        return None
    if len(raw_len) < 4:
        raise ValueError("Incomplete length prefix")
    length = struct.unpack("<I", raw_len)[0]
    raw_payload = sys.stdin.buffer.read(length)
    if len(raw_payload) < length:
        raise ValueError("Incomplete payload")
    return json.loads(raw_payload.decode("utf-8"))


def send_response(response: dict) -> None:
    """Send one 4-byte-prefixed JSON response back to Chrome."""
    encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


# ── Message handlers ─────────────────────────────────────────────────────────
def handle_target_tabs_discovered(msg: dict) -> None:
    """Extension found open tabs that match our target URL pattern."""
    tabs = msg.get("tabs", [])
    trigger = msg.get("trigger", "?")
    log(f"TARGET SITES  trigger={trigger}  count={len(tabs)}")
    for i, tab in enumerate(tabs, 1):
        tab_id  = tab.get("tabId", "?")
        tab_url = tab.get("tabUrl", "?")
        log(f"  [{i}] id={tab_id}  url={tab_url}")


def handle_tab_iframes_discovered(msg: dict) -> None:
    """Extension injected into a target tab and found its iframes."""
    tab_id  = msg.get("tabId", "?")
    tab_url = msg.get("tabUrl", "?")
    iframes = msg.get("iframes", [])

    log(f"IFRAME SCAN   tab={tab_id}  url={tab_url}")

    if len(iframes) == 0:
        log("  → No iframes found on this page")
    elif len(iframes) == 1:
        log(f"  ✓ Connected to the ONLY iframe on site")
        log(f"    iframe url: {iframes[0]}")
    else:
        log(f"  ! Multiple iframes found ({len(iframes)})")
        for i, url in enumerate(iframes, 1):
            log(f"    [{i}] {url}")


def handle_tab_iframes_failed(msg: dict) -> None:
    """Extension could not inject into the tab or find iframes."""
    tab_id  = msg.get("tabId", "?")
    tab_url = msg.get("tabUrl", "?")
    reason  = msg.get("reason", "unknown")
    log(f"IFRAME FAIL   tab={tab_id}  reason={reason}  url={tab_url}")


HANDLERS = {
    "TARGET_TABS_DISCOVERED" : handle_target_tabs_discovered,
    "TAB_IFRAMES_DISCOVERED" : handle_tab_iframes_discovered,
    "TAB_IFRAMES_FAILED"     : handle_tab_iframes_failed,
}


# ── Main loop ────────────────────────────────────────────────────────────────
def main() -> int:
    log("=" * 60)
    log("normal-chrome native host started")
    log(f"Log file: {LOG_FILE}")
    log("Watching: ALL open tabs (any http/https site)")
    log("=" * 60)

    # With connectNative() on the JS side, Chrome keeps this process alive
    # continuously.  We just loop on read_message() indefinitely.
    while True:
        try:
            msg = read_message()
            if msg is None:
                log("stdin closed — Chrome disconnected. Exiting.")
                break

            msg_type = msg.get("type", "")
            handler = HANDLERS.get(msg_type)

            if handler:
                handler(msg)
            else:
                log(f"UNKNOWN MSG   type={msg_type!r}")

            # Every message needs an acknowledgement so Chrome doesn't hang
            send_response({"ok": True, "type": msg_type})

        except KeyboardInterrupt:
            log("Interrupted by user")
            break
        except Exception as exc:
            log(f"ERROR  {exc}")
            try:
                send_response({"ok": False, "error": str(exc)})
            except Exception:
                break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
