"""Generic agentic guards — no task-specific code generation."""

from __future__ import annotations

import re

_ACTION_VERBS = (
    "write", "insert", "add", "create", "edit", "fix", "run", "execute",
    "import", "load", "print", "remove", "delete", "clean", "show", "display",
    "put", "implement", "code", "verify",
)

_INSTRUCTION_MARKERS = (
    "placement",
    "run order",
    "insert below",
    "insert a new",
    "create new cell",
    "first execute cell",
    "you should run",
    "click run",
    "manually",
    "directly below cell",
)


def is_actionable_notebook_request(prompt: str) -> bool:
    text = str(prompt or "").strip().lower()
    if len(text) < 8:
        return False
    if any(v in text for v in _ACTION_VERBS):
        return True
    if re.search(r"/kaggle/input/", text):
        return True
    if re.search(r"\bcell\s*\d+\b", text):
        return True
    return False


def user_requests_run(prompt: str) -> bool:
    text = str(prompt or "").lower()
    needles = (
        " and run", " then run", " run it", " run those", " run these",
        " execute those", " execute them", " execute it", " run the cell",
        " execute the cell", " and execute", " run cell", " execute cell",
        " and run it", "now run", "run them", "run all",
    )
    if any(n in text for n in needles):
        return True
    return bool(re.search(r"\b(run|execute)\b.*\b(cells?|these|them)\b", text))


def is_write_only_request(prompt: str) -> bool:
    return is_actionable_notebook_request(prompt) and not user_requests_run(prompt)


def is_run_verify_request(prompt: str) -> bool:
    text = str(prompt or "").lower()
    return user_requests_run(prompt) and any(
        w in text for w in ("verify", "check output", "validate", "confirm")
    )


def parse_last_n_cells_request(prompt: str) -> int | None:
    """Parse 'run last 3 cells' / 'last three code cells' (digits only)."""
    text = str(prompt or "").lower()
    m = re.search(r"\blast\s+(\d+)\s+(?:code\s+)?cells?\b", text)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 50:
                return n
        except (TypeError, ValueError):
            pass
    return None


def list_code_cell_indices(registry, url: str) -> list[int]:
    try:
        listing = registry.call("notebook_list_cells", {"url": url})
    except Exception:
        return []
    if not isinstance(listing, dict):
        return []
    out: list[int] = []
    for cell in listing.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        if str(cell.get("type") or "code").lower() != "code":
            continue
        try:
            out.append(int(cell.get("index")))
        except (TypeError, ValueError):
            continue
    return out


def resolve_wanted_run_cells(
    user_prompt: str,
    parsed_calls: list,
    *,
    registry,
    url: str,
) -> list[int]:
    """
    Infer full run queue from user prompt (e.g. last N code cells).
    Merges with explicit run_cell indices from the LLM batch.
    """
    explicit: list[int] = []
    for call in parsed_calls or []:
        name = getattr(call, "name", None) or (call.get("name") if isinstance(call, dict) else None)
        if name != "run_cell":
            continue
        args = getattr(call, "args", None) or (call.get("args") if isinstance(call, dict) else {}) or {}
        try:
            explicit.append(int(args.get("cell_index")))
        except (TypeError, ValueError):
            pass

    n = parse_last_n_cells_request(user_prompt)
    if n is not None and registry is not None:
        code_indices = list_code_cell_indices(registry, url)
        if len(code_indices) >= n:
            return code_indices[-n:]
    if explicit:
        return sorted(set(explicit))
    return []


def looks_like_instruction_only_response(text: str) -> bool:
    body = str(text or "").strip()
    if len(body) < 80:
        return False
    low = body.lower()
    hits = sum(1 for m in _INSTRUCTION_MARKERS if m in low)
    return hits >= 2


def agentic_must_continue_with_tools(
    *,
    prompt: str,
    followup_text: str,
    tools_executed: int,
    pipeline_active: bool,
) -> bool:
    if pipeline_active:
        return True
    if not is_actionable_notebook_request(prompt):
        return False
    if tools_executed == 0:
        return True
    return looks_like_instruction_only_response(followup_text)


def build_action_nudge(prompt: str, *, tools_executed: int, round_idx: int) -> str:
    return (
        "Agentic mode: respond with tool_calls, not manual notebook instructions. "
        f"tools_executed={tools_executed}, round={round_idx}.\n"
        f"Task: {prompt.strip()}"
    )
