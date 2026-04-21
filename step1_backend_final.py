#!/usr/bin/env python3
"""
Step 1: Three methods for normal Chrome + extension native messaging
Method 1: listen_for_native_messages() - reads from extension
Method 2: receive_and_print_tab_info() - processes tab data
Method 3: receive_and_print_iframe_info() - processes iframe data
"""
import json
import struct
import sys
from datetime import datetime, timezone


def now_iso() -> str:
    """Get current timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat()


def listen_for_native_messages():
    """
    METHOD 1: Listen for native messages from Chrome extension
    Reads 4-byte length prefix + JSON payload (Chrome native messaging protocol)
    """
    print(f"[{now_iso()}] METHOD 1: Listening for native messages from extension...", file=sys.stderr, flush=True)
    
    while True:
        try:
            # Read 4-byte length prefix
            raw_length = sys.stdin.buffer.read(4)
            if not raw_length or len(raw_length) < 4:
                print(f"[{now_iso()}] No more data from extension. Exiting.", file=sys.stderr, flush=True)
                break
            
            length = struct.unpack("<I", raw_length)[0]
            
            # Read payload
            raw_payload = sys.stdin.buffer.read(length)
            if len(raw_payload) < length:
                print(f"[{now_iso()}] Incomplete payload received. Exiting.", file=sys.stderr, flush=True)
                break
            
            # Parse JSON message
            try:
                message = json.loads(raw_payload.decode("utf-8"))
            except Exception as exc:
                print(f"[{now_iso()}] Failed to parse JSON: {exc}", file=sys.stderr, flush=True)
                continue
            
            # Route message to appropriate handler
            msg_type = message.get("type")
            
            if msg_type == "TAB_IFRAMES_DISCOVERED":
                receive_and_print_iframe_info(message)
            elif msg_type == "TAB_IFRAMES_FAILED":
                receive_and_print_iframe_failure(message)
            elif msg_type == "SCAN_COMPLETE" or msg_type == "TARGET_TABS_DISCOVERED":
                # Handle both types for better visibility
                pass 
            else:
                print(f"[{now_iso()}] Unknown message type: {msg_type}", file=sys.stderr, flush=True)
            
            # Send acknowledgment back to extension
            send_response({"ok": True, "type": msg_type})
            
        except Exception as exc:
            print(f"[{now_iso()}] Exception in message loop: {exc}", file=sys.stderr, flush=True)
            break


def receive_and_print_iframe_info(message: dict):
    """
    METHOD 2: Receive and print tab connection info from extension
    Called when extension successfully connected to a tab
    """
    print(f"\n[{now_iso()}] METHOD 2: Tab Connection Info Received", file=sys.stderr, flush=True)
    
    tab_id = message.get("tabId", "?")
    tab_url = message.get("tabUrl", "?")
    
    print(f"  Connected to Tab ID: {tab_id}", flush=True)
    print(f"  Tab URL: {tab_url}", flush=True)


def receive_and_print_iframe_failure(message: dict):
    """
    Receive and print iframe discovery failure from extension
    """
    print(f"\n[{now_iso()}] iframe Discovery Failed", file=sys.stderr, flush=True)
    
    tab_id = message.get("tabId", "?")
    tab_url = message.get("tabUrl", "?")
    reason = message.get("reason", "unknown")
    
    print(f"  Tab ID: {tab_id}", flush=True)
    print(f"  Tab URL: {tab_url}", flush=True)
    print(f"  Reason: {reason}", flush=True)


def receive_and_print_iframe_info(message: dict):
    """
    Receive and print iframe discovery info from extension
    """
    tab_id = message.get("tabId", "?")
    tab_url = message.get("tabUrl", "?")
    iframes = message.get("iframes", [])
    
    print(f"\n[{now_iso()}] Target Site Found: {tab_url} (Tab Content: {tab_id})", flush=True)
    
    if len(iframes) == 1:
        print(f"  ✓ SUCCESS: Connected to the only iframe on site", flush=True)
        print(f"    Iframe URL: {iframes[0]}", flush=True)
    elif len(iframes) > 1:
        print(f"  ! WARNING: Found multiple iframes ({len(iframes)})", flush=True)
        for i, url in enumerate(iframes):
            print(f"    [{i+1}] {url}", flush=True)
    else:
        print(f"  ? INFO: No iframes found on this site", flush=True)


def receive_and_print_scan_complete(message: dict):
    """
    Receive scan completion notification from extension
    """
    matching_tabs = message.get("matchingTabs", 0)
    scanned_at = message.get("scannedAt", "?")
    
    print(f"\n[{now_iso()}] Scan Complete: {matching_tabs} matching tab(s) found", file=sys.stderr, flush=True)


def send_response(response: dict):
    """Send response back to extension"""
    try:
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception as exc:
        print(f"[{now_iso()}] Failed to send response: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    print(f"\n{'='*70}", file=sys.stderr, flush=True)
    print(f"General Iframe Scraper Backend - Normal Chrome + Extension", file=sys.stderr, flush=True)
    print(f"{'='*70}", file=sys.stderr, flush=True)
    print(f"[{now_iso()}] Listening for messages...", file=sys.stderr, flush=True)
    print(f"  - Target Site Discovery", file=sys.stderr, flush=True)
    print(f"  - Iframe Connection Confirmation", file=sys.stderr, flush=True)
    print(f"{'='*70}\n", file=sys.stderr, flush=True)
    
    try:
        listen_for_native_messages()
        return 0
    except KeyboardInterrupt:
        print(f"\n[{now_iso()}] Interrupted by user", file=sys.stderr, flush=True)
        return 0
    except Exception as exc:
        print(f"\n[{now_iso()}] Fatal error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
