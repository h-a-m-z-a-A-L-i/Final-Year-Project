"""Host-side ReAct helpers — auto-chain atomic tools when the user intent is clear."""

from __future__ import annotations

import re
from typing import Any


def extract_cell_content_from_prompt(prompt: str) -> str | None:
    """Pull code/content from prompts like 'insert below cell 2 with print(\"hi\")'."""
    text = str(prompt or "").strip()
    if not text:
        return None

    # Parenthesized code at end: ... (print("hamza"))
    paren_code = re.search(r"\(\s*(print\s*\([^)]+\))\s*\)\s*$", text, re.I)
    if paren_code:
        return paren_code.group(1).strip()

    patterns = (
        r"\bwith\s+(.+)$",
        r"\bcontaining\s+(.+)$",
        r"\bcontent\s*[:=]\s*(.+)$",
        r"\(\s*([^)]+)\s*\)\s*$",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I | re.S)
        if not m:
            continue
        raw = m.group(1).strip().rstrip(".!?")
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        return raw.strip() or None
    return None


def infer_new_cell_index(insert_args: dict, insert_result: dict) -> int | None:
    """Resolve 1-based label for the cell created by insert_cell."""
    for key in ("new_cell_index", "cell_index", "app_index"):
        raw = insert_result.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass

    anchor = insert_args.get("index")
    if anchor is None:
        anchor = insert_args.get("cell_index")
    if anchor is None:
        return None
    try:
        anchor = int(anchor)
    except (TypeError, ValueError):
        return None

    direction = str(insert_args.get("direction") or "below").strip().lower()
    if direction == "above":
        return anchor
    return anchor + 1


def insert_timed_out_but_likely_ok(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("ok") is True:
        return False
    err = str(result.get("error") or "").lower()
    return "timeout" in err


def build_edit_after_insert(
    user_prompt: str,
    insert_args: dict,
    insert_result: dict,
    *,
    url: str,
    tab_id: int | None = None,
) -> dict | None:
    """If the user asked for content with a new cell, return edit_cell_by_index args."""
    content = extract_cell_content_from_prompt(user_prompt)
    if not content:
        return None

    usable = bool(insert_result.get("ok")) or insert_timed_out_but_likely_ok(insert_result)
    if not usable:
        return None

    cell_index = infer_new_cell_index(insert_args, insert_result)
    if cell_index is None:
        return None

    out: dict[str, Any] = {
        "cell_index": cell_index,
        "content": content,
        "url": url,
    }
    if isinstance(tab_id, int) and tab_id > 0:
        out["tab_id"] = tab_id
    return out


def extract_target_cell_index(prompt: str) -> int | None:
    """Parse 'in cell 1', 'cell 1', 'into cell 3' from user prompt."""
    text = str(prompt or "").strip()
    if not text:
        return None
    patterns = (
        r"\b(?:in|into|to)\s+cell\s*#?\s*(\d+)\b",
        r"\bcell\s*#?\s*(\d+)\b",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                pass
    return None


def build_direct_edit_from_prompt(
    user_prompt: str,
    *,
    url: str,
    tab_id: int | None = None,
) -> dict | None:
    """Direct edit_cell_by_index when user names a cell and code."""
    cell_index = extract_target_cell_index(user_prompt)
    content = extract_cell_content_from_prompt(user_prompt)
    if cell_index is None or not content:
        return None
    out: dict[str, Any] = {
        "cell_index": cell_index,
        "content": content,
        "url": url,
    }
    if isinstance(tab_id, int) and tab_id > 0:
        out["tab_id"] = tab_id
    return out


def prompt_requests_split_cell(prompt: str) -> bool:
    """True when the user wants to reorganize existing cell source across cells."""
    text = str(prompt or "").strip().lower()
    if not text:
        return False
    split_verbs = (
        "split", "divide", "break up", "break apart", "separate", "partition",
        "refactor", "reorganize", "re-organize",
    )
    if not any(v in text for v in split_verbs):
        return False
    if re.search(r"\b(?:cell|code|source|content)\b", text):
        return True
    return extract_target_cell_index(prompt) is not None


def extract_cell_count_from_prompt(prompt: str) -> int | None:
    """Parse 'create 5 cells', 'add 3 new cells', etc."""
    text = str(prompt or "").strip()
    if not text:
        return None
    if prompt_requests_split_cell(text):
        return None
    patterns = (
        r"\b(?:create|add|insert|make)\s+(\d+)\s+(?:new\s+)?cells?\b",
        r"\b(\d+)\s+(?:new\s+)?cells?\b",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            try:
                n = int(m.group(1))
                if 2 <= n <= 25:
                    return n
            except (TypeError, ValueError):
                pass
    return None


def extract_delete_cell_index(prompt: str) -> int | None:
    """Parse 'delete cell 2', 'remove cell 3' from user prompt."""
    text = str(prompt or "").strip()
    if not text:
        return None
    patterns = (
        r"\b(?:delete|remove)\s+cell\s*(?:index)?\s*#?\s*(\d+)\b",
        r"\b(?:delete|remove)\s+(?:the\s+)?cell\s+at\s+(?:index\s+)?#?\s*(\d+)\b",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                pass
    return None


def prompt_requests_insert(prompt: str) -> bool:
    text = str(prompt or "").lower()
    needles = (
        "insert",
        "create new cell",
        "create a new cell",
        "add new cell",
        "add a new cell",
        "make new cell",
        "make a new cell",
        "new cell under",
        "new cell below",
        "new cell after",
    )
    return any(n in text for n in needles)


def prompt_requests_delete(prompt: str) -> bool:
    text = str(prompt or "").lower()
    return bool(
        re.search(r"\b(?:delete|remove)\b", text)
        and re.search(r"\bcell\b", text)
    )


def extract_insert_anchor_from_prompt(prompt: str) -> int | None:
    """Parse anchor for inserts: 'under cell index 2', 'below cell 2'."""
    text = str(prompt or "").strip()
    if not text:
        return None
    patterns = (
        r"\b(?:under|below|after)\s+cell\s*(?:index)?\s*#?\s*(\d+)\b",
        r"\b(?:under|below|after)\s+index\s*#?\s*(\d+)\b",
        r"\binsert\s+(?:\d+\s+cells?\s+)?(?:under|below)\s+(?:cell\s*)?#?\s*(\d+)\b",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                pass
    return extract_target_cell_index(prompt)


def parse_multi_cell_contents(prompt: str, count: int) -> list[str]:
    """Build per-cell source for N new cells (e.g. print 1..N)."""
    text = str(prompt or "")
    contents: list[str] = []

    range_match = re.search(
        r"print\s+(\d+)\s*[,]\s*(\d+)\s*[,]\s*(\d+)\s*[,]\s*(\d+)\s*[,]\s*(\d+)",
        text,
        re.I,
    )
    if range_match:
        for g in range_match.groups():
            if g:
                contents.append(f"print({int(g)})")

    if not contents:
        list_match = re.search(r"print\s+([\d,\s]+)", text, re.I)
        if list_match:
            for part in re.split(r"[\s,]+", list_match.group(1).strip()):
                if part.isdigit():
                    contents.append(f"print({int(part)})")

    if len(contents) < count and re.search(
        r"\bprint\s+(?:them|each|1\s*[,]?\s*2|one\s+in\s+each)\b", text, re.I
    ):
        for i in range(1, count + 1):
            contents.append(f"print({i})")

    while len(contents) < count:
        contents.append("")

    return contents[:count]
