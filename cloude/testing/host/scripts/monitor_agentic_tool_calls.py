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

  # Filter by notebook slug
  python testing/host/scripts/monitor_agentic_tool_calls.py testing-ol

Then open the copilot, select Agentic mode, and send a message.

Options:
  --from-start   Replay existing log lines, then follow new ones
  --poll SEC     Poll interval when waiting for the log file (default 0.25)
  --verbose      Pretty-print full payload JSON under each CALL/RESULT/EXEC
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


def _priority_keys_for_tool(tool: str) -> tuple[str, ...]:
    name = str(tool or "").strip().lower()
    if name == "delete_by_index":
        return ("cell_index", "tab_id", "tabId")
    if name == "insert_cell":
        return ("index", "direction", "content", "cell_type")
    if name in ("edit_cell_by_index", "edit_cell"):
        return ("cell_index", "content", "mode")
    if name in ("run_cell", "run_cell_by_index"):
        return ("cell_index", "tab_id", "tabId")
    if name.startswith("notebook_"):
        return ("url", "cell_index", "index", "slug", "tab_id", "tabId")
    return ("cell_index", "index", "direction", "content", "url", "mode", "tab_id", "tabId")


_RESULT_PRIORITY_KEYS = ("ok", "cell_index", "index", "error", "message", "status", "detail")


def _format_scalar(val: object, *, key: str = "") -> str:
    if key == "content" and isinstance(val, str):
        text = val.replace("\n", "\\n")
        if len(text) > 80:
            text = text[:79] + "…"
        return json.dumps(text, ensure_ascii=False)
    return json.dumps(val, ensure_ascii=False, default=str)


def _format_payload_block(
    tool: str,
    data: dict,
    *,
    verbose: bool,
    label: str = "payload",
    indent: str = "  ",
) -> str:
    if not isinstance(data, dict) or not data:
        return ""
    if verbose:
        body = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        indented = "\n".join(f"{indent}  {line}" for line in body.splitlines())
        return f"{indent}{label}:\n{indented}"

    priority = (
        _priority_keys_for_tool(tool)
        if label == "payload"
        else _RESULT_PRIORITY_KEYS
    )
    lines = [f"{indent}{label}:"]
    shown: set[str] = set()
    for key in priority:
        if key in data and data[key] not in (None, ""):
            lines.append(f"{indent}  {key}: {_format_scalar(data[key], key=key)}")
            shown.add(key)
    for key, val in data.items():
        if key in shown or val in (None, ""):
            continue
        lines.append(f"{indent}  {key}: {_format_scalar(val, key=key)}")
    return "\n".join(lines)


def _format_payload_sections(tool: str, row: dict, *, verbose: bool) -> str:
    sections: list[str] = []
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    if args:
        block = _format_payload_block(tool, args, verbose=verbose, label="payload")
        if block:
            sections.append(block)
    if row.get("event") in ("result", "exec"):
        result_data = row.get("result") if isinstance(row.get("result"), dict) else {}
        if result_data:
            block = _format_payload_block(
                tool,
                result_data,
                verbose=verbose,
                label="result",
            )
            if block:
                sections.append(block)
    return "\n".join(sections)


def _round_tag(row: dict) -> str:
    rnd = row.get("round")
    if rnd is None:
        batch_id = str(row.get("batch_id") or "").strip()
        if batch_id:
            return f" {batch_id}"
        return ""
    return f" r{rnd}"


def _slug_matches(row: dict, needle: str) -> bool:
    if not needle:
        return True
    n = needle.lower().strip()
    if not n:
        return True
    slug = str(row.get("notebook_slug") or "").lower()
    url = str(row.get("url") or "").lower()
    if slug and (n in slug or slug in n):
        return True
    if url and n in url:
        return True
    variants = {n, n.replace("-", "_"), n.replace("_", "-")}
    return any(v in slug or v in url for v in variants if v)


def _fmt_line(row: dict, *, use_color: bool, verbose: bool = False) -> str:
    event = str(row.get("event") or "").strip()
    ts = str(row.get("local_time") or row.get("ts") or "")[:19]
    prefix = f"[{ts}] " if ts else ""

    def c(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if use_color else text

    if event == "session_start":
        mode = row.get("mode") or "?"
        sid = row.get("session_id") or "default"
        url = str(row.get("url") or "")[:90]
        slug = str(row.get("notebook_slug") or "").strip()
        slug_tag = f" [{slug}]" if slug else ""
        lines = [c(f"{prefix}══ CHAT {mode} session={sid} ══", _BOLD + _CYAN)]
        if url:
            lines.append(c(f"{prefix}       notebook{slug_tag}: {url}", _DIM))
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
        batch_id = row.get("batch_id") or f"r{rnd}"
        return c(
            f"{prefix}BATCH  {batch_id} | executing {len(tools)} tool(s) on host",
            _CYAN,
        )

    if event == "batch_end":
        rnd = row.get("round", "?")
        batch_id = row.get("batch_id") or f"r{rnd}"
        ok = row.get("ok")
        status = ""
        if ok is True:
            status = " OK"
        elif ok is False:
            status = " FAIL"
        line = f"{prefix}BATCH  {batch_id} | done{status}"
        detail = str(row.get("detail") or "").strip()
        if detail:
            line += f" | {detail[:120]}"
        return c(line, _GREEN if ok else (_RED if ok is False else _CYAN))

    if event == "dispatch":
        tool = row.get("tool") or "?"
        phase = str(row.get("phase") or "").strip()
        phase_tag = f" [{phase}]" if phase else ""
        line = f"{prefix}CALL{phase_tag}{_round_tag(row)}  {tool}"
        payload = _format_payload_sections(tool, row, verbose=verbose)
        if payload:
            return c(line, _MAGENTA) + "\n" + c(payload, _DIM)
        return c(line, _MAGENTA)

    if event == "result":
        tool = row.get("tool") or "?"
        ok = bool(row.get("ok"))
        phase = str(row.get("phase") or "").strip()
        phase_tag = f" [{phase}]" if phase else ""
        status = "OK" if ok else "FAIL"
        line = f"{prefix}RESULT{phase_tag}{_round_tag(row)}  {tool} → {status}"
        err = str(row.get("error") or "").strip()
        if err:
            line += f" | {err[:120]}"
        payload = _format_payload_sections(tool, row, verbose=verbose)
        if payload:
            return c(line, _GREEN if ok else _RED) + "\n" + c(payload, _DIM)
        return c(line, _GREEN if ok else _RED)

    if event == "exec":
        tool = row.get("tool") or "?"
        ok = bool(row.get("ok"))
        phase = str(row.get("phase") or "").strip()
        phase_tag = f" [{phase}]" if phase else ""
        status = "OK" if ok else "FAIL"
        line = f"{prefix}EXEC{phase_tag}{_round_tag(row)}  {tool} → {status}"
        err = str(row.get("error") or "").strip()
        if err:
            line += f" | {err[:120]}"
        payload = _format_payload_sections(tool, row, verbose=verbose)
        if payload:
            return c(line, _GREEN if ok else _RED) + "\n" + c(payload, _DIM)
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


def _print_banner(trace_log: Path, *, use_color: bool, notebook_filter: str) -> None:
    def c(t: str, code: str) -> str:
        return f"{code}{t}{_RESET}" if use_color else t

    print(c("Agentic tool-call monitor", _BOLD + _CYAN))
    print(c(f"Tailing: {trace_log}", _DIM))
    if notebook_filter:
        print(c(f"Filter: notebook slug contains {notebook_filter!r}", _DIM))
    print(
        c(
            "Use Agentic mode in the copilot chat. "
            "Events: PARSE → BATCH → CALL → RESULT → VERIFY",
            _DIM,
        )
    )
    print(c("Disable trace: TOOL_CALL_TERMINAL_TRACE=0 in .env", _DIM))
    print(c("─" * 72, _DIM))
    sys.stdout.flush()


def _follow(
    trace_log: Path,
    *,
    from_start: bool,
    poll: float,
    use_color: bool,
    verbose: bool,
    notebook_filter: str,
) -> None:
    _print_banner(trace_log, use_color=use_color, notebook_filter=notebook_filter)
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
                    if notebook_filter and not _slug_matches(row, notebook_filter):
                        continue
                    formatted = _fmt_line(row, use_color=use_color, verbose=verbose)
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
        "notebook",
        nargs="?",
        default="",
        help="Optional notebook slug filter (e.g. testing-ol)",
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
        "--verbose",
        action="store_true",
        help="Pretty-print full payload JSON under each CALL/RESULT/EXEC",
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
            verbose=args.verbose,
            notebook_filter=str(args.notebook or "").strip(),
        )
    except KeyboardInterrupt:
        print("\n[monitor] stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
