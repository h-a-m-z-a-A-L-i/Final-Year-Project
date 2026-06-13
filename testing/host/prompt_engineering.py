"""Load notebook chat prompts and build Cerebras-ready message lists."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    from .config import ALLOWED_MODES, MAX_CONTEXT_CHARS, MAX_NOTEBOOK_CONTEXT_CHARS
except Exception:
    from config import ALLOWED_MODES, MAX_CONTEXT_CHARS, MAX_NOTEBOOK_CONTEXT_CHARS

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TOOL_PROMPTS_DIR = PROMPTS_DIR / "tool_calling"
BASE_PROMPT_FILE = PROMPTS_DIR / "base_notebook_assistant.txt"
JUPYTER_STRUCTURE_FILE = PROMPTS_DIR / "jupyter_structure.txt"

_MODE_FILE_MAP = {
    "ask": PROMPTS_DIR / "ask.txt",
    "code": PROMPTS_DIR / "code.txt",
}

_MODE_LABELS = {
    "ask": "Ask",
    "code": "Code",
}

_LEGACY_MODE_ALIASES = {
    "auto": "ask",
    "simple": "ask",
    "dependency": "ask",
    "explain_error": "ask",
    "explain_code": "ask",
    "code_review": "ask",
    "general": "ask",
    "general help": "ask",
}

_SECTION_HEADING = re.compile(
    r"^##\s+(Role|Task|Specifics|Context|Examples|Notes)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_ERROR_HINT_PATTERN = re.compile(
    r"\b(traceback|stack\s*trace|exception|error:|failed|nameerror|typeerror|"
    r"keyerror|indexerror|valueerror|attributeerror|importerror|syntaxerror|"
    r"runtimeerror|zerodivisionerror|cell execution failed|execution error|fix\s+error)\b",
    re.IGNORECASE,
)

_DEPENDENCY_HINT_PATTERN = re.compile(
    r"\b(dependency|dependencies|upstream|downstream|execution order|"
    r"run before|must run first|affected cells|cell graph|depends on)\b",
    re.IGNORECASE,
)

_REVIEW_HINT_PATTERN = re.compile(
    r"\b(code review|review\s+(this|my|the)|security issue|vulnerability|refactor|"
    r"best practice|code smell|audit|lint|data leakage)\b",
    re.IGNORECASE,
)

_PLACEMENT_HINT_PATTERN = re.compile(
    r"\b("
    r"where\s+(should|to|can|do|would)|which\s+cell|cell\s+position|"
    r"insert\s+(this|the|code|a|it)?|new\s+cell|after\s+cell|before\s+cell|"
    r"closer\s+(cell|position|spot)|put\s+(this|the|code|it)\s+|"
    r"place\s+(this|the|code|it)\s+"
    r")\b",
    re.IGNORECASE,
)

# Appended last in system prompts (lost-in-the-middle mitigation).
_SYSTEM_NOTES_ASK = """\
- Read CONTEXT_MANIFEST and TARGET_CELL_STATUS before citing any cell.
- Only cite cells in `listed_cells` or notebook evidence — never invent cell contents.
- **Empty target cell:** acknowledge it, ask what the user wants there, suggest 1–2 flow-appropriate next steps — no code dump yet.
- Reply directly to the user. No meta preamble ("User asks…", "I will explain…", "Let me think…").
- Do not echo notebook UI actions (Insert below, Create new cell, Copy, Edit cell) — describe cells by index only.
- Use clean Markdown: heading, bullets or numbered steps. No raw JSON or graph dumps.
- If `coverage` is none or partial and evidence is missing, start with **INSUFFICIENT_CONTEXT** and one question."""

_SYSTEM_NOTES_CODE = """\
- Read CONTEXT_MANIFEST and TARGET_CELL_STATUS before citing any cell.
- Only cite cells in `listed_cells` or tool results — never invent cell contents.
- **Empty target cell:** ask what the user wants, suggest 1–2 flow-appropriate options — no Placement/Code until they confirm.
- For new scripts: recommend **Insert Code Cell Below** the defining cell, not a random empty cell far away.
- User-facing reply: Placement bullets + one `python` code block when generating code. No duplicate blocks.
- If `coverage` is none or partial and tools lack data, start with **INSUFFICIENT_CONTEXT**.
- When calling tools, pass the exact session notebook URL from Context."""


def _system_notes_tail(mode: str) -> str:
    return _SYSTEM_NOTES_CODE if normalize_mode(mode) == "code" else _SYSTEM_NOTES_ASK


def list_chat_modes() -> list[dict[str, str]]:
    return [{"id": m, "label": _MODE_LABELS.get(m, m)} for m in sorted(ALLOWED_MODES)]


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def parse_prompt_sections(raw: str) -> dict[str, str]:
    """Split a prompt file into Role / Task / Specifics / Context / Examples / Notes."""
    text = str(raw or "").strip()
    if not text:
        return {}

    sections: dict[str, str] = {}
    matches = list(_SECTION_HEADING.finditer(text))
    if not matches:
        return {"role": text}

    for i, match in enumerate(matches):
        key = match.group(1).lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections[key] = body
    return sections


def load_prompt_sections(path: Path) -> dict[str, str]:
    return parse_prompt_sections(_read_text(path))


def load_mode_sections(mode: str) -> dict[str, str]:
    mode = normalize_mode(mode)
    path = _MODE_FILE_MAP.get(mode) or _MODE_FILE_MAP["ask"]
    return load_prompt_sections(path)


def load_base_sections() -> dict[str, str]:
    return parse_prompt_sections(_read_text(BASE_PROMPT_FILE))


def load_tool_prompt_sections(*, include_examples: bool = True) -> tuple[str, str, str]:
    system_tool = _read_text(TOOL_PROMPTS_DIR / "local_read_tools.txt")
    if not system_tool:
        system_tool = _read_text(TOOL_PROMPTS_DIR / "system_prompt.txt")

    descriptions = _read_text(TOOL_PROMPTS_DIR / "local_tool_descriptions_autogen.txt")
    if not descriptions:
        try:
            from .tool_registry import build_local_tool_descriptions
        except Exception:
            from tool_registry import build_local_tool_descriptions
        descriptions = build_local_tool_descriptions()

    examples = ""
    if include_examples:
        examples = _read_text(TOOL_PROMPTS_DIR / "tool_examples_autogen.txt")
        if examples:
            try:
                from .local_notebook_tools import LOCAL_TOOL_NAMES
            except Exception:
                from local_notebook_tools import LOCAL_TOOL_NAMES
            ex_lines = []
            for line in examples.splitlines():
                name = line.split(" example args:", 1)[0].strip()
                if name in LOCAL_TOOL_NAMES:
                    ex_lines.append(line)
            examples = "\n".join(ex_lines)
        if not examples:
            examples = _read_text(TOOL_PROMPTS_DIR / "examples.txt")
    return system_tool, descriptions, examples


def normalize_mode(mode: str | None) -> str:
    m = str(mode or "ask").strip().lower()
    m = _LEGACY_MODE_ALIASES.get(m, m)
    return m if m in ALLOWED_MODES else "ask"


def detect_mode(
    prompt: str,
    explicit_mode: str | None = None,
    *,
    has_cell_context: bool = False,
) -> str:
    """Use the UI-selected mode only (Ask or Code). Legacy `auto` maps to Ask."""
    _ = prompt, has_cell_context
    return normalize_mode(explicit_mode or "ask")


def classify_ask_intent(prompt: str) -> str:
    """Sub-intent for context packing inside ask mode (not exposed in UI)."""
    text = str(prompt or "")
    if _PLACEMENT_HINT_PATTERN.search(text):
        return "placement"
    if _ERROR_HINT_PATTERN.search(text):
        return "error"
    if _DEPENDENCY_HINT_PATTERN.search(text):
        return "dependency"
    if _REVIEW_HINT_PATTERN.search(text):
        return "review"
    if _extract_cell_number(text) is not None:
        return "explain"
    return "general"


def _extract_cell_number(prompt: str):
    try:
        from .prompt_utils import _extract_cell_number as extract
    except Exception:
        from prompt_utils import _extract_cell_number as extract
    return extract(prompt)


def _section_block(title: str, *bodies: str) -> str:
    parts = [str(b).strip() for b in bodies if b and str(b).strip()]
    if not parts:
        return ""
    return f"## {title}\n" + "\n\n".join(parts)


def merge_context_with_profile(notebook_context: str, profile_context: str) -> str:
    parts = []
    if profile_context:
        parts.append(profile_context.strip())
    if notebook_context:
        parts.append(notebook_context.strip())
    merged = "\n\n".join(parts)
    notebook_budget = MAX_NOTEBOOK_CONTEXT_CHARS if MAX_NOTEBOOK_CONTEXT_CHARS > 0 else None
    if notebook_budget is not None and len(merged) > MAX_CONTEXT_CHARS + notebook_budget:
        if profile_context and len(profile_context) < MAX_CONTEXT_CHARS:
            budget = MAX_CONTEXT_CHARS + notebook_budget - len(profile_context)
            if notebook_context and len(notebook_context) > budget:
                notebook_context = notebook_context[:budget] + "\n...[merged context truncated]"
            parts = [profile_context.strip(), notebook_context.strip()] if notebook_context else [profile_context.strip()]
            merged = "\n\n".join(parts)
    return merged


def build_system_content(
    mode: str,
    *,
    notebook_url: str = "",
    context: str = "",
    include_tools: bool = True,
    include_tool_examples: bool | None = None,
) -> str:
    """
    Assemble system prompt using the Role → Task → Specifics → Context → Examples → Notes schema.
    Notes (and hard rules) are last to reduce lost-in-the-middle failures.
    """
    mode = normalize_mode(mode)
    base = load_base_sections()
    mode_secs = load_mode_sections(mode)

    notebook_len = len(context or "")
    if include_tool_examples is None:
        include_tool_examples = notebook_len < MAX_NOTEBOOK_CONTEXT_CHARS // 2

    tool_system, tool_desc, tool_examples = ("", "", "")
    if include_tools:
        tool_system, tool_desc, tool_examples = load_tool_prompt_sections(
            include_examples=include_tool_examples
        )

    jupyter_model = _read_text(JUPYTER_STRUCTURE_FILE)
    if jupyter_model:
        jupyter_model = parse_prompt_sections(jupyter_model)
        jupyter_body = "\n\n".join(
            jupyter_model.get(k, "")
            for k in ("role", "context", "notes")
            if jupyter_model.get(k)
        )
    else:
        jupyter_body = ""

    context_parts: list[str] = []
    if jupyter_body:
        context_parts.append(f"### Jupyter notebook model\n{jupyter_body}")
    if base.get("context"):
        context_parts.append(base["context"])
    if notebook_url:
        context_parts.append(f"Notebook URL: {notebook_url}")
    if include_tools:
        context_parts.append(
            "When calling tools, always pass this exact notebook URL in the `url` argument."
        )
    context_parts.append(
        "Ground answers only in CONTEXT_MANIFEST, notebook evidence below, and tool/prefetched results."
    )
    if tool_system:
        context_parts.append(f"### Tool calling\n{tool_system}")
    if tool_desc:
        context_parts.append(f"### Available tools\n{tool_desc}")
    if context:
        context_parts.append(f"### Notebook evidence\n{str(context).strip()}")

    example_parts: list[str] = []
    if mode_secs.get("examples"):
        example_parts.append(mode_secs["examples"])
    if tool_examples:
        example_parts.append(f"### Tool argument examples\n{tool_examples}")

    notes_parts: list[str] = []
    if mode_secs.get("notes"):
        notes_parts.append(mode_secs["notes"])
    if base.get("notes"):
        notes_parts.append(base["notes"])
    notes_parts.append(_system_notes_tail(mode))

    ordered_blocks = [
        _section_block(
            "Role",
            base.get("role"),
            mode_secs.get("role"),
            f"Active UI mode: **{mode}** ({_MODE_LABELS.get(mode, mode)}).",
        ),
        _section_block("Task", mode_secs.get("task")),
        _section_block("Specifics", mode_secs.get("specifics")),
        _section_block("Context", *context_parts),
        _section_block("Examples", *example_parts),
        _section_block("Notes", *notes_parts),
    ]

    return "\n\n".join(block for block in ordered_blocks if block)


def build_chat_messages(
    *,
    mode: str,
    user_prompt: str,
    history: list[dict] | None,
    context: str = "",
    notebook_url: str = "",
    include_tools: bool = True,
) -> list[dict[str, Any]]:
    try:
        from .context_budget import trim_history_for_api
    except Exception:
        from context_budget import trim_history_for_api

    mode = normalize_mode(mode)
    api_history = trim_history_for_api(history)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": build_system_content(
                mode,
                notebook_url=notebook_url,
                context=context,
                include_tools=include_tools,
            ),
        }
    ]
    for h in api_history:
        role = str(h.get("role", "")).strip().lower()
        content = str(h.get("content", ""))
        if role in {"user", "assistant", "system"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": str(user_prompt or "")})
    return messages
