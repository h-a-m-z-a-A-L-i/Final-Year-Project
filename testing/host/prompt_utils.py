import re


_CELL_PROMPT_RE = re.compile(r"(?:\(|\[)?\s*cell\s*#?\s*(\d+)\s*(?:\)|\])?", re.IGNORECASE)
_NAME_PATTERNS = [
    re.compile(r"\bmy\s+name\s+is\s+(.+?)(?:[.!?,;:]|$)", re.IGNORECASE),
    re.compile(r"\bcall\s+me\s+(.+?)(?:[.!?,;:]|$)", re.IGNORECASE),
    re.compile(r"\bi\s+am\s+(.+?)(?:[.!?,;:]|$)", re.IGNORECASE),
]


def _extract_cell_number(prompt: str):
    text = str(prompt or "")
    match = _CELL_PROMPT_RE.search(text)
    if not match:
        return None
    try:
        number = int(match.group(1))
        return number if number > 0 else None
    except Exception:
        return None


def _extract_user_profile_facts(prompt: str) -> dict:
    text = str(prompt or "").strip()
    if not text:
        return {}

    facts = {}
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        name = match.group(1).strip().strip('"\'')
        name = re.sub(r"\s+", " ", name)
        if name:
            facts["name"] = name
            break
    return facts


def _build_profile_memory_context(facts: dict) -> str:
    if not isinstance(facts, dict) or not facts:
        return ""
    lines = []
    for key in sorted(facts.keys()):
        value = str(facts.get(key) or "").strip()
        if value:
            lines.append(f"- {key}: {value}")
    if not lines:
        return ""
    return "Known user facts:\n" + "\n".join(lines)