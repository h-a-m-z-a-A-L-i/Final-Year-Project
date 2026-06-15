"""Frozen notebook baseline + per-turn deltas for Cerebras prefix prompt caching.

The static prefix (system instructions + tool schemas + session-start notebook snapshot)
must stay byte-identical across turns. Live notebook changes are sent only on the current
user turn via a delta block (and optional prefetched get_cell), never rewritten into
system content or prior chat history.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import (
        BASELINE_MAX_CELL_INPUT_CHARS,
        BASELINE_MAX_CELL_OUTPUT_CHARS,
        BASELINE_MAX_TOTAL_CHARS,
        DATA_ROOT,
        MAX_FULL_NOTEBOOK_CONTEXT_CHARS,
    )
    from .notebook_context import (
        _cells_from_data,
        _truncate_at_cell_boundaries,
        load_notebook_snapshot,
    )
    from .persistence_helpers import get_safe_filename, read_json_file
except Exception:
    from config import (
        BASELINE_MAX_CELL_INPUT_CHARS,
        BASELINE_MAX_CELL_OUTPUT_CHARS,
        BASELINE_MAX_TOTAL_CHARS,
        DATA_ROOT,
        MAX_FULL_NOTEBOOK_CONTEXT_CHARS,
    )
    from notebook_context import (
        _cells_from_data,
        _truncate_at_cell_boundaries,
        load_notebook_snapshot,
    )
    from persistence_helpers import get_safe_filename, read_json_file


BASELINE_DIR = DATA_ROOT / "meta" / "prompt_baselines"
BASELINE_FORMAT_VERSION = 2


def cerebras_static_cache_enabled() -> bool:
    try:
        from .config import CEREBRAS_STATIC_NOTEBOOK_CACHE, LLM_PROVIDER
    except Exception:
        from config import CEREBRAS_STATIC_NOTEBOOK_CACHE, LLM_PROVIDER
    if str(LLM_PROVIDER or "").lower() != "cerebras":
        return False
    return bool(CEREBRAS_STATIC_NOTEBOOK_CACHE)


def effective_session_id(session_id: str, mode: str) -> str:
    """Optional mode-scoped session for separate chat memory + cache keys."""
    try:
        from .config import CHAT_SESSION_PER_MODE
    except Exception:
        from config import CHAT_SESSION_PER_MODE
    sid = str(session_id or "default").strip() or "default"
    mode = str(mode or "ask").strip().lower() or "ask"
    if CHAT_SESSION_PER_MODE:
        return f"{sid}::mode::{mode}"
    return sid


def _baseline_path(history_key: str, session_id: str, mode: str) -> Path:
    raw = f"{history_key}|{session_id}|{mode}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    try:
        from .notebook_storage import notebook_filename
    except Exception:
        try:
            from notebook_storage import notebook_filename
        except Exception:
            from testing.host.notebook_storage import notebook_filename
    prefix = notebook_filename(history_key).replace(".json", "")[:48]
    return BASELINE_DIR / f"{prefix}_{digest}.json"


def _cell_fingerprint(cell: dict) -> dict[str, Any]:
    try:
        idx = int(cell.get("index", 0))
    except Exception:
        idx = 0
    return {
        "index": idx,
        "type": str(cell.get("type") or "code"),
        "input": str(cell.get("input") or ""),
        "output": str(cell.get("output") or ""),
    }


def _truncate_text(text: str, max_chars: int) -> str:
    s = str(text or "")
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    return s[:max_chars] + "\n... [truncated]"


def _format_compact_cell_block(cell: dict) -> str:
    try:
        idx = int(cell.get("index", 0))
    except Exception:
        idx = 0
    ctype = str(cell.get("type") or "code")
    inp = _truncate_text(str(cell.get("input") or ""), int(BASELINE_MAX_CELL_INPUT_CHARS))
    lines = [f"### Cell [{idx}] ({ctype})"]
    lang = "python" if ctype == "code" else "markdown"
    lines.extend(["input:", f"```{lang}", inp or "(empty)", "```"])
    if ctype == "code":
        out = _truncate_text(str(cell.get("output") or ""), int(BASELINE_MAX_CELL_OUTPUT_CHARS))
        if out.strip():
            lines.extend(["output:", "```", out, "```"])
        else:
            lines.append("output: (none)")
    return "\n".join(lines)


def pack_baseline_notebook_text(url: str) -> tuple[str, list[dict], str]:
    """Compact notebook snapshot for the frozen baseline (session start)."""
    data, source = load_notebook_snapshot(url)
    cells = _cells_from_data(data)
    if not cells:
        return (
            "CONTEXT_MANIFEST\ncoverage: none\nsnapshot: none\n\n"
            "No notebook snapshot available at session start.",
            [],
            source,
        )
    header = (
        "CONTEXT_MANIFEST\ncoverage: baseline\nsnapshot: "
        f"{source}\n"
        "## Session baseline notebook (frozen for prompt cache)\n"
        "Compact snapshot at session start — outputs truncated. "
        "Use the live delta block on the current turn for full latest input/output.\n"
    )
    blocks = [header]
    for cell in sorted(cells, key=lambda c: int(c.get("index", 0) or 0)):
        blocks.append(_format_compact_cell_block(cell))
    body = "\n\n".join(blocks)
    total_limit = int(BASELINE_MAX_TOTAL_CHARS or 0)
    if total_limit > 0 and len(body) > total_limit:
        body = _truncate_at_cell_boundaries(body, total_limit)
    elif int(MAX_FULL_NOTEBOOK_CONTEXT_CHARS or 0) > 0 and len(body) > int(MAX_FULL_NOTEBOOK_CONTEXT_CHARS):
        body = _truncate_at_cell_boundaries(body, int(MAX_FULL_NOTEBOOK_CONTEXT_CHARS))
    return body, [_cell_fingerprint(c) for c in cells], source


def get_or_create_baseline(
    *,
    history_key: str,
    session_id: str,
    mode: str,
    url: str,
) -> tuple[str, list[dict], bool]:
    """Return (baseline_text, baseline_cells, created_new)."""
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    path = _baseline_path(history_key, session_id, mode)
    existing = read_json_file(path)
    if (
        isinstance(existing, dict)
        and existing.get("baseline_text")
        and int(existing.get("format_version") or 0) == BASELINE_FORMAT_VERSION
    ):
        cells = existing.get("cells")
        if isinstance(cells, list):
            return str(existing["baseline_text"]), cells, False

    text, cells, source = pack_baseline_notebook_text(url)
    record = {
        "format_version": BASELINE_FORMAT_VERSION,
        "history_key": history_key,
        "session_id": session_id,
        "mode": mode,
        "url": url,
        "snapshot": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline_text": text,
        "cells": cells,
    }
    try:
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return text, cells, True


def compute_notebook_delta(
    baseline_cells: list[dict],
    live_cells: list[dict],
    *,
    max_changed: int = 24,
    max_output_chars: int = 6000,
) -> str:
    """Compact diff: new/changed cells since session baseline."""
    base_by_idx = {
        int(c.get("index", 0)): c for c in baseline_cells if isinstance(c, dict)
    }
    live_fps = [_cell_fingerprint(c) for c in live_cells if isinstance(c, dict)]
    live_by_idx = {c["index"]: c for c in live_fps}

    changed: list[dict] = []
    added: list[dict] = []

    for idx, live in sorted(live_by_idx.items()):
        base = base_by_idx.get(idx)
        if base is None:
            added.append(live)
            continue
        if (
            live.get("type") != base.get("type")
            or live.get("input") != base.get("input")
            or live.get("output") != base.get("output")
        ):
            changed.append({"index": idx, "before": base, "after": live})

    removed = sorted(set(base_by_idx) - set(live_by_idx))
    if not changed and not added and not removed:
        return ""

    lines = [
        "## Live notebook delta (since session baseline)",
        "Only cells that changed after this chat session started. "
        "Prefer this over the frozen baseline for current input/output.",
    ]
    if changed:
        lines.append("### Changed cells")
        for item in changed[:max_changed]:
            after = dict(item["after"])
            out = str(after.get("output") or "")
            if max_output_chars > 0 and len(out) > max_output_chars:
                after["output"] = out[:max_output_chars] + "\n... [output truncated]"
            lines.append(
                json.dumps(
                    {"cell_index": item["index"], "current": after},
                    ensure_ascii=False,
                )
            )
        if len(changed) > max_changed:
            lines.append(f"... and {len(changed) - max_changed} more changed cells")

    if added:
        lines.append("### New cells (not in baseline)")
        for cell in added[:max_changed]:
            out = str(cell.get("output") or "")
            if max_output_chars > 0 and len(out) > max_output_chars:
                cell = dict(cell)
                cell["output"] = out[:max_output_chars] + "\n... [output truncated]"
            lines.append(json.dumps({"cell_index": cell["index"], "current": cell}, ensure_ascii=False))

    if removed:
        lines.append("### Removed cell indices")
        lines.append(", ".join(str(i) for i in removed[:max_changed]))

    return "\n".join(lines)


def build_turn_tail(
    *,
    delta_block: str,
    profile_context: str = "",
    prefetch_block: str = "",
) -> str:
    """Dynamic content for the current turn only (appended to the latest user message)."""
    parts: list[str] = []
    if delta_block.strip():
        parts.append(delta_block.strip())
    if profile_context.strip():
        parts.append("## User profile (session facts)\n" + profile_context.strip())
    if prefetch_block.strip():
        parts.append(prefetch_block.strip())
    return "\n\n".join(parts)


def prepare_static_cache_context(
    *,
    history_key: str,
    session_id: str,
    mode: str,
    url: str,
    profile_context: str = "",
) -> tuple[str, str]:
    """
    Returns (system_notebook_context, turn_tail_without_user_prompt).
    system_notebook_context is frozen; turn_tail goes on the current user message only.
    """
    baseline_text, baseline_cells, _created = get_or_create_baseline(
        history_key=history_key,
        session_id=session_id,
        mode=mode,
        url=url,
    )
    _data, _source = load_notebook_snapshot(url)
    live_cells = _cells_from_data(_data)
    delta = compute_notebook_delta(baseline_cells, live_cells)
    tail = build_turn_tail(delta_block=delta, profile_context=profile_context)
    return baseline_text, tail
