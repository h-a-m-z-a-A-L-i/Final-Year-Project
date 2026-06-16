"""Real-time agentic tool-call trace for stderr and JSONL (monitor script tails the log)."""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .tool_execution_audit import _tool_names_from_calls
except Exception:
    try:
        from tool_execution_audit import _tool_names_from_calls
    except Exception:
        def _tool_names_from_calls(tool_calls):  # type: ignore
            names: list[str] = []
            for tc in tool_calls or []:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    n = str(fn.get("name") or "").strip()
                    if n:
                        names.append(n)
            return names

_LOCK = threading.Lock()


def _trace_log_path() -> Path:
    try:
        from .config import DATA_ROOT
    except Exception:
        from config import DATA_ROOT
    return DATA_ROOT / "logs" / "agentic_tool_trace.jsonl"


def enabled() -> bool:
    raw = os.environ.get("TOOL_CALL_TERMINAL_TRACE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def trace_log_path() -> Path:
    """Path tailed by scripts/monitor_agentic_tool_calls.py."""
    return _trace_log_path()


def _now() -> str:
    try:
        return datetime.now().strftime("%H:%M:%S")
    except Exception:
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_event(event: str, **fields: Any) -> None:
    if not enabled():
        return
    payload: dict[str, Any] = {"event": event, "ts": _now_iso(), "local_time": _now()}
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    try:
        path = _trace_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def _emit(line: str) -> None:
    if not enabled():
        return
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def notebook_slug_from_url(url: str) -> str:
    """Extract Kaggle notebook slug from an /edit URL (empty if unknown)."""
    try:
        from .kaggle_kernel_client import parse_kaggle_edit_url
    except Exception:
        try:
            from kaggle_kernel_client import parse_kaggle_edit_url
        except Exception:
            return ""
    parsed = parse_kaggle_edit_url(str(url or "").strip())
    if not parsed:
        return ""
    _owner, slug = parsed
    return slug


def _batch_id(round_idx: int | None) -> str | None:
    if round_idx is None:
        return None
    return f"r{round_idx}"


def _sanitize_result_payload(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    out: dict[str, Any] = {}
    for key, val in result.items():
        if isinstance(val, str) and len(val) > 200:
            out[key] = val[:199] + "…"
        else:
            out[key] = val
    return out


def _short_args(args: dict[str, Any] | None, *, max_len: int = 100) -> str:
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


def trace_session_start(*, mode: str, session_id: str | None, url: str = "") -> None:
    slug = notebook_slug_from_url(url)
    _emit(f"[{_now()}] ══ CHAT {mode} session={session_id or 'default'} ══")
    if url:
        slug_tag = f" [{slug}]" if slug else ""
        _emit(f"[{_now()}]        notebook{slug_tag}: {url[:90]}")
    _append_event(
        "session_start",
        mode=mode,
        session_id=session_id,
        url=url,
        notebook_slug=slug or None,
    )


def trace_react_round(round_idx: int) -> None:
    _emit(f"[{_now()}] ── ReAct round {round_idx} ──")
    _append_event("react_round", round=round_idx)


def trace_tools_parsed(
    round_idx: int,
    tool_calls: list[dict] | None,
    *,
    source: str = "native",
    recovery: bool = False,
    parse_errors: list[str] | None = None,
) -> None:
    names = _tool_names_from_calls(tool_calls)
    if not names:
        if parse_errors:
            _emit(f"[{_now()}] PARSE  r{round_idx} | 0 tools | errors: {'; '.join(parse_errors)[:160]}")
        else:
            _emit(f"[{_now()}] PARSE  r{round_idx} | 0 tools ({source})")
    else:
        suffix = " [recovery]" if recovery else ""
        _emit(f"[{_now()}] PARSE  r{round_idx} | {len(names)} tool(s){suffix}: {', '.join(names)}")
    _append_event(
        "parse",
        round=round_idx,
        tools=names,
        tool_count=len(names),
        source=source,
        recovery=recovery,
        parse_errors=parse_errors or [],
    )


def trace_tool_reorder(
    round_idx: int,
    before: list[str],
    after: list[str],
) -> None:
    """Log host-side stable reorder (run tools moved after writes)."""
    if before == after:
        return
    _emit(
        f"[{_now()}] REORDER r{round_idx} | "
        f"{', '.join(before)} → {', '.join(after)}"
    )
    _append_event(
        "reorder",
        round=round_idx,
        before=before,
        after=after,
    )


def trace_dispatch_path(path: str, detail: str = "") -> None:
    line = f"[{_now()}] PATH   {path}"
    if detail:
        line += f" | {detail}"
    _emit(line)
    _append_event("dispatch_path", path=path, detail=detail)


def trace_batch_start(round_idx: int, tool_calls: list[dict] | None) -> None:
    names = _tool_names_from_calls(tool_calls)
    batch_id = _batch_id(round_idx)
    _emit(f"[{_now()}] BATCH  r{round_idx} | executing {len(names)} tool(s) on host")
    _append_event(
        "batch_start",
        round=round_idx,
        batch_id=batch_id,
        tools=names,
        tool_count=len(names),
    )


def trace_batch_end(round_idx: int, *, ok: bool | None = None, detail: str = "") -> None:
    batch_id = _batch_id(round_idx)
    status = ""
    if ok is True:
        status = " OK"
    elif ok is False:
        status = " FAIL"
    line = f"[{_now()}] BATCH  r{round_idx} | done{status}"
    if detail:
        line += f" | {detail[:120]}"
    _emit(line)
    _append_event(
        "batch_end",
        round=round_idx,
        batch_id=batch_id,
        ok=ok,
        detail=detail or None,
    )


def log_tool_call(
    tool: str,
    args: dict[str, Any] | None,
    *,
    phase: str = "",
    round_idx: int | None = None,
    batch_id: str | None = None,
    notebook_slug: str | None = None,
) -> None:
    """Record tool dispatch (before host executes the call)."""
    arg_text = _short_args(args)
    phase_tag = f" [{phase}]" if phase else ""
    rnd_tag = f" r{round_idx}" if round_idx is not None else ""
    line = f"[{_now()}] CALL{phase_tag}{rnd_tag}  {tool or '?'}"
    if arg_text:
        line += f" ({arg_text})"
    _emit(line)
    _append_event(
        "dispatch",
        tool=tool,
        args=args if isinstance(args, dict) else {},
        phase=phase or None,
        round=round_idx,
        batch_id=batch_id or _batch_id(round_idx),
        notebook_slug=notebook_slug or None,
    )


def log_tool_result(
    tool: str,
    args: dict[str, Any] | None,
    result: dict[str, Any] | None,
    *,
    phase: str = "",
    round_idx: int | None = None,
    batch_id: str | None = None,
    notebook_slug: str | None = None,
) -> None:
    """Record tool result (after host returns)."""
    ok = bool((result or {}).get("ok")) if isinstance(result, dict) else False
    status = "OK" if ok else "FAIL"
    arg_text = _short_args(args)
    phase_tag = f" [{phase}]" if phase else ""
    rnd_tag = f" r{round_idx}" if round_idx is not None else ""
    line = f"[{_now()}] RESULT{phase_tag}{rnd_tag}  {tool or '?'}"
    if arg_text:
        line += f" ({arg_text})"
    line += f" → {status}"
    err = ""
    if not ok and isinstance(result, dict):
        err = str(result.get("error") or result.get("message") or "").strip()
        if err:
            line += f" | {err[:120]}"
    _emit(line)
    _append_event(
        "result",
        tool=tool,
        args=args if isinstance(args, dict) else {},
        result=_sanitize_result_payload(result),
        ok=ok,
        phase=phase or None,
        round=round_idx,
        batch_id=batch_id or _batch_id(round_idx),
        notebook_slug=notebook_slug or None,
        error=err or None,
        result_summary=status,
    )


def trace_tool_exec(
    tool: str,
    args: dict[str, Any] | None,
    result: dict[str, Any] | None,
    *,
    phase: str = "",
    round_idx: int | None = None,
    batch_id: str | None = None,
    notebook_slug: str | None = None,
) -> None:
    """Record completed tool execution (result only; use log_tool_call before dispatch)."""
    log_tool_result(
        tool,
        args,
        result,
        phase=phase,
        round_idx=round_idx,
        batch_id=batch_id,
        notebook_slug=notebook_slug,
    )
    ok = bool((result or {}).get("ok")) if isinstance(result, dict) else False
    err = ""
    if not ok and isinstance(result, dict):
        err = str(result.get("error") or result.get("message") or "").strip()
    _append_event(
        "exec",
        tool=tool,
        args=args if isinstance(args, dict) else {},
        result=_sanitize_result_payload(result),
        ok=ok,
        phase=phase or None,
        round=round_idx,
        batch_id=batch_id or _batch_id(round_idx),
        notebook_slug=notebook_slug or None,
        error=err or None,
        result_summary="OK" if ok else "FAIL",
    )


def trace_verification(round_idx: int, verification: dict[str, Any] | None) -> None:
    v = verification if isinstance(verification, dict) else {}
    verified = v.get("verified")
    goal = v.get("goal_verified")
    strict = v.get("strict_goal_verified")
    queue_status = v.get("tool_queue_status")
    if queue_status is None:
        if v.get("tool_queue_complete") or v.get("run_queue_complete"):
            queue_status = "complete"
        elif v.get("needs_fix"):
            queue_status = "error"
        else:
            queue_status = "active"
    _emit(
        f"[{_now()}] VERIFY r{round_idx} | verified={verified} goal={goal} "
        f"strict={strict} queue={queue_status}"
    )
    exec_err = v.get("execution_error") or {}
    if isinstance(exec_err, dict) and exec_err:
        cell = exec_err.get("cell_index")
        summary = exec_err.get("error_summary") or exec_err.get("error") or ""
        if summary or cell is not None:
            _emit(f"[{_now()}]        exec_error cell={cell}: {str(summary)[:160]}")
    reason = v.get("goal_reason")
    if reason and not verified:
        _emit(f"[{_now()}]        goal_reason: {str(reason)[:160]}")
    executed = v.get("executed") or []
    if executed:
        parts = []
        for ex in executed[:8]:
            if not isinstance(ex, dict):
                continue
            t = ex.get("tool") or "?"
            d = "ok" if ex.get("dispatched") else "fail"
            parts.append(f"{t}:{d}")
        if parts:
            _emit(f"[{_now()}]        executed: {', '.join(parts)}")
    _append_event(
        "verify",
        round=round_idx,
        verified=verified,
        goal_verified=goal,
        strict_goal_verified=strict,
        queue_status=queue_status,
        goal_reason=reason,
        execution_error=exec_err if isinstance(exec_err, dict) else None,
        executed=executed if isinstance(executed, list) else [],
    )


def trace_run_error(
    failed_cell_index: int,
    error_output: str,
    *,
    round_idx: int | None = None,
    notebook_slug: str | None = None,
    pending: list[int] | None = None,
) -> None:
    """Log run-queue stop on cell execution error."""
    analysis_preview = str(error_output or "").strip().replace("\n", " ")[:160]
    pending_tag = f" pending={pending}" if pending else ""
    _emit(
        f"[{_now()}] RUN_ERROR r{round_idx or '?'} | cell={failed_cell_index}{pending_tag} | "
        f"{analysis_preview}"
    )
    _append_event(
        "RUN_ERROR",
        round=round_idx,
        batch_id=_batch_id(round_idx),
        notebook_slug=notebook_slug or None,
        failed_cell_index=int(failed_cell_index),
        pending_run_cells=list(pending or []),
        error_preview=analysis_preview or None,
    )


def trace_batch_success(
    cell_count: int,
    *,
    run_completed: list[int] | None = None,
    round_idx: int | None = None,
    notebook_slug: str | None = None,
) -> None:
    """Log clean completion of all run_cell tools in a batch."""
    runs = list(run_completed or [])
    runs_tag = f" runs={runs}" if runs else ""
    _emit(
        f"[{_now()}] BATCH_SUCCESS r{round_idx or '?'} | "
        f"{cell_count} cell op(s){runs_tag}"
    )
    _append_event(
        "BATCH_SUCCESS",
        round=round_idx,
        batch_id=_batch_id(round_idx),
        notebook_slug=notebook_slug or None,
        cell_count=int(cell_count),
        run_completed=runs or None,
    )


def trace_react_stop(round_idx: int, reason: str = "") -> None:
    _emit(f"[{_now()}] STOP   r{round_idx} | {reason or 'react loop ended'}")
    _append_event("react_stop", round=round_idx, reason=reason or "react loop ended")


def trace_prose_only(round_idx: int, streak: int) -> None:
    _emit(f"[{_now()}] PROSE  r{round_idx} | no tools in response (streak={streak})")
    _append_event("prose_only", round=round_idx, streak=streak)


def trace_llm_error(round_idx: int, message: str) -> None:
    _emit(f"[{_now()}] LLMERR r{round_idx} | {str(message)[:200]}")
    _append_event("llm_error", round=round_idx, message=str(message)[:500])
