import re


_CELL_PATTERNS = [
    # "cell 3", "cell#3", "cell(3)", "cell3"
    re.compile(r"(?:\(|\[)?\s*cell\s*#?\s*(\d+)\s*(?:\)|\])?", re.IGNORECASE),
    re.compile(r"\bcell(\d+)\b", re.IGNORECASE),
    # "dependencies for cell 5", "index 2", "idx #4"
    re.compile(
        r"\b(?:dependencies?|deps?|upstream|downstream)\s+(?:of|for)\s+cell\s*#?\s*(\d+)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:cell[_\s-]*)?(?:index|idx)\s*#?\s*(\d+)\b", re.IGNORECASE),
    # "3rd cell", "1st cell"
    re.compile(r"\b(\d+)(?:st|nd|rd|th)\s+cell\b", re.IGNORECASE),
    # ordinals without digit
    re.compile(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+cell\b", re.IGNORECASE),
]

_ORDINAL_CELL = {
    "first": 0,
    "second": 1,
    "third": 2,
    "fourth": 3,
    "fifth": 4,
    "sixth": 5,
    "seventh": 6,
    "eighth": 7,
    "ninth": 8,
    "tenth": 9,
}
_NAME_PATTERNS = [
    re.compile(r"\bmy\s+name\s+is\s+(.+?)(?:[.!?,;:]|$)", re.IGNORECASE),
    re.compile(r"\bcall\s+me\s+(.+?)(?:[.!?,;:]|$)", re.IGNORECASE),
    re.compile(r"\bi\s+am\s+(.+?)(?:[.!?,;:]|$)", re.IGNORECASE),
]


_LAST_CELL_HINT = re.compile(r"\b(last|final)\s+(?:code\s+)?cell\b", re.IGNORECASE)
_ERROR_OUTPUT = re.compile(
    r"\b(traceback|error:|exception|keyerror|nameerror|typeerror|valueerror|syntaxerror)\b",
    re.IGNORECASE,
)


def _extract_cell_number(prompt: str):
    text = str(prompt or "")
    if not text.strip():
        return None

    for pattern in _CELL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(1)
        if raw.isdigit():
            return int(raw)
        word = str(raw).strip().lower()
        if word in _ORDINAL_CELL:
            return _ORDINAL_CELL[word]

    return None


def resolve_cell_index(prompt: str, cells: list | None) -> int | None:
    """Resolve 1-based cell index from prompt + notebook cells (e.g. 'last cell' → last error cell)."""
    explicit = _extract_cell_number(prompt)
    if explicit is not None:
        return explicit
    if not cells or not _LAST_CELL_HINT.search(str(prompt or "")):
        return None

    code_rows: list[tuple[int, dict]] = []
    for cell in cells:
        if not isinstance(cell, dict) or str(cell.get("type") or "code") != "code":
            continue
        try:
            idx = int(cell.get("index", 0))
        except (TypeError, ValueError):
            continue
        if idx >= 1:
            code_rows.append((idx, cell))
    if not code_rows:
        return None

    with_error = [
        idx
        for idx, cell in code_rows
        if _ERROR_OUTPUT.search(str(cell.get("output") or ""))
    ]
    if with_error:
        return max(with_error)

    nonempty = [idx for idx, cell in code_rows if str(cell.get("input") or "").strip()]
    if nonempty:
        return max(nonempty)
    return max(idx for idx, _ in code_rows)


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
    return "Known user facts (this conversation only):\n" + "\n".join(lines)