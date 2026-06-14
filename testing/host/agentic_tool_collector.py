"""Collect multiple tool_calls across Cerebras micro-rounds, then execute as one host batch."""

from __future__ import annotations

import json
import re
from typing import Any, Callable


def _collect_nudge(prompt: str, collected: list[dict]) -> str:
    try:
        from .agentic_action_guard import parse_last_n_cells_request, user_requests_run
    except Exception:
        from agentic_action_guard import parse_last_n_cells_request, user_requests_run

    run_count = sum(1 for tc in collected if _tool_name(tc) == "run_cell")
    n = parse_last_n_cells_request(prompt)
    if user_requests_run(prompt) and n and run_count < n:
        return (
            f"Same task — emit run_cell for the last {n} code cells "
            f"({n - run_count} run_cell calls still needed). One tool per message is OK."
        )
    floor = expected_tool_floor(prompt)
    if floor and len(collected) < floor:
        return (
            f"Same task — {floor - len(collected)} tool_calls still needed "
            f"({len(collected)}/{floor} collected). Emit the next one."
        )
    return (
        "Continue the SAME user task. Emit ONLY the remaining tool_calls not yet sent. "
        "If every required tool was already emitted, respond with no tool_calls."
    )


def _tool_name(tc: dict) -> str:
    fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
    return str(fn.get("name") or "").strip()


def expected_tool_floor(prompt: str) -> int:
    text = str(prompt or "").lower()
    floor = 0
    floor += len(re.findall(r"\binsert", text))
    floor += len(re.findall(r"\bedit", text))
    floor += len(re.findall(r"\brun_cell\b", text))
    floor += len(re.findall(r"\brun cell", text))
    try:
        from .agentic_action_guard import parse_last_n_cells_request, user_requests_run
    except Exception:
        from agentic_action_guard import parse_last_n_cells_request, user_requests_run
    n = parse_last_n_cells_request(prompt)
    if n and user_requests_run(prompt):
        return max(floor, n)
    return floor


def collection_satisfied(
    prompt: str,
    collected: list[dict],
    *,
    registry=None,
    url: str = "",
) -> bool:
    if not collected:
        return False
    try:
        from .agentic_action_guard import resolve_wanted_run_cells, user_requests_run
    except Exception:
        from agentic_action_guard import resolve_wanted_run_cells, user_requests_run

    run_count = sum(1 for tc in collected if _tool_name(tc) == "run_cell")
    floor = expected_tool_floor(prompt)

    try:
        from .agentic_action_guard import parse_last_n_cells_request, user_requests_run
    except Exception:
        from agentic_action_guard import parse_last_n_cells_request, user_requests_run

    n = parse_last_n_cells_request(prompt)
    if user_requests_run(prompt) and n and run_count >= n:
        return True

    if registry is not None and user_requests_run(prompt):
        try:
            from .agentic_batch_executor import _parse_tool_calls
        except Exception:
            from agentic_batch_executor import _parse_tool_calls
        parsed = _parse_tool_calls(collected, url=url, tab_id=None)
        wanted = resolve_wanted_run_cells(prompt, parsed, registry=registry, url=url)
        if wanted and run_count >= len(wanted):
            return True

    if floor >= 2 and len(collected) >= floor:
        if user_requests_run(prompt):
            return run_count >= max(1, prompt.lower().count("run"))
        return True

    if not user_requests_run(prompt) and any(
        _tool_name(tc) in {"insert_cell", "edit_cell_by_index"} for tc in collected
    ):
        return len(collected) >= max(2, floor) or floor == 0

    return False


def expand_tool_batch_via_llm(
    *,
    tool_messages: list[dict[str, Any]],
    tools: list[dict],
    llm_create: Callable[..., Any],
    create_kwargs_base: dict[str, Any],
    user_prompt: str,
    registry=None,
    url: str = "",
    max_rounds: int = 7,
) -> tuple[list[dict], int]:
    """
    Cerebras returns one tool_call per completion — collect until the batch is complete.
    Returns (merged_tool_calls, collection_rounds).
    """
    if not tool_messages:
        return [], 0

    last = tool_messages[-1]
    if last.get("role") != "assistant":
        return [], 0

    collected: list[dict] = list(last.get("tool_calls") or [])
    if not collected:
        return [], 0

    if collection_satisfied(user_prompt, collected, registry=registry, url=url):
        return collected, 0

    if len(collected) > 1:
        return collected, 0

    msgs = list(tool_messages)
    collection_rounds = 0

    for _ in range(max_rounds):
        if collection_satisfied(user_prompt, collected, registry=registry, url=url):
            break

        last_asst = msgs[-1]
        for tc in last_asst.get("tool_calls") or []:
            tid = tc.get("id") if isinstance(tc, dict) else None
            if not tid:
                continue
            msgs.append({
                "role": "tool",
                "tool_call_id": tid,
                "content": json.dumps({"ok": True, "queued": True, "note": "host batch collector"}),
            })
        msgs.append({"role": "user", "content": _collect_nudge(user_prompt, collected)})

        kwargs = dict(create_kwargs_base)
        kwargs["messages"] = msgs
        kwargs["tool_choice"] = "auto"
        resp = llm_create(**kwargs)
        dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
        msg = ((dumped.get("choices") or [{}])[0].get("message") or {})
        new_tcs = msg.get("tool_calls") or []
        collection_rounds += 1

        if not new_tcs:
            break

        collected.extend(new_tcs)
        msgs.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": new_tcs,
        })

        if collection_satisfied(user_prompt, collected, registry=registry, url=url):
            break

    return collected, collection_rounds
