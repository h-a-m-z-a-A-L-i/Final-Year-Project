#!/usr/bin/env python3
"""
Live terminal monitor for agentic tool calls while using the chat UI.

Writes come from host.py (agentic mode) via testing/host/tool_call_terminal.py
into data/logs/agentic_tool_trace.jsonl when TOOL_CALL_TERMINAL_TRACE=1 (default).

Run in a dedicated terminal alongside host.py:

  # Terminal 1
  python testing/host/host.py

  # Terminal 2
  python testing/host/scripts/monitor_agentic_tool_calls.py

Then open the copilot, select Agentic mode, and send a message.

Options:
  --from-start   Replay existing log lines, then follow new ones
  --poll SEC     Poll interval when waiting for the log file (default 0.25)
  --no-color     Plain text output
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HOST_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_LOG = _HOST_DIR / "data" / "logs" / "agentic_tool_trace.jsonl"

# ANSI (disable with --no-color)
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"
_BLUE = "\033[34m"


def _short_args(args: dict | None, *, max_len: int = 100) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    skip = {"url", "tab_id", "tabId"}
    parts: list[str] = []
    for key in ("cell_index", "index", "direction", "content", "mode"):
        if key in args and args[key] not in (None, ""):
            val = args[key]
            if key == "content" and isinstance(val, str):
                val = val.replace("\n", "\\n")[:40]
            parts.append(f"{key}={val!r}")
    for key, val in args.items():
        if key in skip or key in ("cell_index", "index", "direction", "content", "mode"):
            continue
        if val in (None, ""):
            continue
        parts.append(f"{key}={val!r}")
        if len(parts) >= 4:
            break
    text = " ".join(parts)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _fmt_line(row: dict, *, use_color: bool) -> str:
    event = str(row.get("event") or "").strip()
    ts = str(row.get("local_time") or row.get("ts") or "")[:19]
    prefix = f"[{ts}] " if ts else ""

    def c(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if use_color else text

    if event == "session_start":
        mode = row.get("mode") or "?"
        sid = row.get("session_id") or "default"
        url = str(row.get("url") or "")[:90]
        lines = [c(f"{prefix}══ CHAT {mode} session={sid} ══", _BOLD + _CYAN)]
        if url:
            lines.append(c(f"{prefix}       notebook: {url}", _DIM))
        return "\n".join(lines)

    if event == "react_round":
        return c(f"{prefix}── ReAct round {row.get('round', '?')} ──", _BLUE)

    if event == "parse":
        tools = row.get("tools") or []
        rnd = row.get("round", "?")
        source = row.get("source") or "native"
        recovery = " [recovery]" if row.get("recovery") else ""
        errors = row.get("parse_errors") or []
        if not tools:
            if errors:
                err = "; ".join(str(e) for e in errors)[:160]
                return c(f"{prefix}PARSE  r{rnd} | 0 tools | errors: {err}", _YELLOW)
            return c(f"{prefix}PARSE  r{rnd} | 0 tools ({source})", _YELLOW)
        names = ", ".join(str(t) for t in tools)
        return c(
            f"{prefix}PARSE  r{rnd} | {len(tools)} tool(s){recovery}: {names}",
            _MAGENTA,
        )

    if event == "dispatch_path":
        detail = str(row.get("detail") or "").strip()
        path = row.get("path") or "?"
        line = f"{prefix}PATH   {path}"
        if detail:
            line += f" | {detail}"
        return c(line, _DIM)

    if event == "batch_start":
        tools = row.get("tools") or []
        rnd = row.get("round", "?")
        return c(
            f"{prefix}BATCH  r{rnd} | executing {len(tools)} tool(s) on host",
            _CYAN,
        )

    if event == "exec":
        tool = row.get("tool") or "?"
        ok = bool(row.get("ok"))
        phase = str(row.get("phase") or "").strip()
        phase_tag = f" [{phase}]" if phase else ""
        arg_text = _short_args(row.get("args") if isinstance(row.get("args"), dict) else {})
        status = "OK" if ok else "FAIL"
        line = f"{prefix}EXEC{phase_tag}  {tool}"
        if arg_text:
            line += f" ({arg_text})"
        line += f" → {status}"
        err = str(row.get("error") or "").strip()
        if err:
            line += f" | {err[:120]}"
        return c(line, _GREEN if ok else _RED)

    if event == "verify":
        rnd = row.get("round", "?")
        line = (
            f"{prefix}VERIFY r{rnd} | verified={row.get('verified')} "
            f"goal={row.get('goal_verified')} strict={row.get('strict_goal_verified')} "
            f"queue={row.get('queue_status')}"
        )
        out = [c(line, _YELLOW)]
        exec_err = row.get("execution_error")
        if isinstance(exec_err, dict) and exec_err:
            cell = exec_err.get("cell_index")
            summary = exec_err.get("error_summary") or exec_err.get("error") or ""
            if summary or cell is not None:
                out.append(c(f"{prefix}       exec_error cell={cell}: {str(summary)[:160]}", _RED))
        reason = row.get("goal_reason")
        if reason and not row.get("verified"):
            out.append(c(f"{prefix}       goal_reason: {str(reason)[:160]}", _DIM))
        executed = row.get("executed") or []
        if isinstance(executed, list) and executed:
            parts = []
            for ex in executed[:8]:
                if not isinstance(ex, dict):
                    continue
                t = ex.get("tool") or "?"
                d = "ok" if ex.get("dispatched") else "fail"
                parts.append(f"{t}:{d}")
            if parts:
                out.append(c(f"{prefix}       executed: {', '.join(parts)}", _DIM))
        return "\n".join(out)

    if event == "react_stop":
        return c(
            f"{prefix}STOP   r{row.get('round', '?')} | {row.get('reason') or 'react loop ended'}",
            _BLUE,
        )

    if event == "prose_only":
        return c(
            f"{prefix}PROSE  r{row.get('round', '?')} | no tools (streak={row.get('streak')})",
            _DIM,
        )

    if event == "llm_error":
        return c(
            f"{prefix}LLMERR r{row.get('round', '?')} | {str(row.get('message') or '')[:200]}",
            _RED,
        )

    return c(f"{prefix}{event} {json.dumps(row, ensure_ascii=False, default=str)[:200]}", _DIM)


def _print_banner(trace_log: Path, *, use_color: bool) -> None:
    def c(t: str, code: str) -> str:
        return f"{code}{t}{_RESET}" if use_color else t

    print(c("Agentic tool-call monitor", _BOLD + _CYAN))
    print(c(f"Tailing: {trace_log}", _DIM))
    print(c("Use Agentic mode in the copilot chat. Events: PARSE → BATCH/PATH → EXEC → VERIFY", _DIM))
    print(c("Disable trace: TOOL_CALL_TERMINAL_TRACE=0 in .env", _DIM))
    print(c("─" * 72, _DIM))
    sys.stdout.flush()


def _follow(trace_log: Path, *, from_start: bool, poll: float, use_color: bool) -> None:
    _print_banner(trace_log, use_color=use_color)
    offset = 0
    if not from_start and trace_log.is_file():
        offset = trace_log.stat().st_size

    while True:
        if not trace_log.is_file():
            time.sleep(poll)
            continue
        try:
            with trace_log.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                while True:
                    line = fh.readline()
                    if not line:
                        break
                    offset = fh.tell()
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        print(text, flush=True)
                        continue
                    if not isinstance(row, dict):
                        continue
                    formatted = _fmt_line(row, use_color=use_color)
                    if formatted:
                        print(formatted, flush=True)
        except OSError as exc:
            print(f"[monitor] read error: {exc}", file=sys.stderr, flush=True)
        time.sleep(poll)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live terminal view of agentic tool calls from the chat UI.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_TRACE_LOG,
        help=f"JSONL trace file (default: {DEFAULT_TRACE_LOG})",
    )
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="Replay the whole log, then follow new lines",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=0.25,
        help="Seconds between polls when idle (default: 0.25)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Plain text output",
    )
    args = parser.parse_args()
    use_color = not args.no_color and sys.stdout.isatty()

    try:
        _follow(
            args.log.resolve(),
            from_start=args.from_start,
            poll=max(0.05, float(args.poll)),
            use_color=use_color,
        )
    except KeyboardInterrupt:
        print("\n[monitor] stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
