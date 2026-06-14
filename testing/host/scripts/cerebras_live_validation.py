#!/usr/bin/env python3
"""
Live Cerebras GPT-OSS validation (no mocks). Skips if no API key.

Logs to testing/host/data/logs/cerebras_live_validation.jsonl

Usage:
  python testing/host/scripts/cerebras_live_validation.py
  python testing/host/scripts/cerebras_live_validation.py --task 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("AGENTIC_TEXT_TOOLS", "1")
os.environ.setdefault("ENABLE_TPM_PREFLIGHT", "0")

LOG_PATH = REPO / "testing/host/data/logs/cerebras_live_validation.jsonl"

TASKS = {
    1: "Edit cell 10 to print('live_test_1') and run it.",
    2: "Insert a new code cell below cell 5 with print('live_test_2') and run it.",
    3: (
        "Create a 3-cell pipeline below cell 3: cell A imports os, cell B prints os.getcwd(), "
        "cell C prints 'done'. Insert all three cells, edit each, and run each."
    ),
    4: "Fix cell 10 if it has an error, then run cell 10.",
    5: (
        "Step 1: read cell 1 with notebook_get_cell. Step 2: edit cell 2 to print('step2'). "
        "Step 3: run cell 2. Use one tool batch per host round as needed."
    ),
}


def _has_api_key() -> bool:
    try:
        from testing.host.config import CEREBRAS_API_KEY, CEREBRAS_SECONDARY_API_KEY, _LLM_CLIENT
    except Exception:
        from config import CEREBRAS_API_KEY, CEREBRAS_SECONDARY_API_KEY, _LLM_CLIENT
    return bool(_LLM_CLIENT and (CEREBRAS_API_KEY or CEREBRAS_SECONDARY_API_KEY))


def _log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_task(task_id: int, url: str) -> dict:
    from testing.host.config import LLM_MODEL, _LLM_CLIENT
    from testing.host.prompt_engineering import build_chat_messages
    from testing.host.agentic_text_tools import parse_text_tool_batch_result
    from testing.host.context_budget import estimate_messages_tokens, messages_for_api

    prompt = TASKS[task_id]
    messages = build_chat_messages(
        mode="agentic",
        user_prompt=prompt,
        history=[],
        context="Notebook has 25 code cells for live validation.",
        notebook_url=url,
        include_tools=True,
        text_tool_calls=True,
    )
    messages[-1]["_react_original_user"] = True
    t0 = time.monotonic()
    resp = _LLM_CLIENT.chat.completions.create(
        messages=messages_for_api(messages),
        model=LLM_MODEL,
        temperature=0.3,
    )
    wall_ms = (time.monotonic() - t0) * 1000
    raw = ""
    if hasattr(resp, "model_dump"):
        raw = ((resp.model_dump().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    else:
        raw = str(resp)
    parsed = parse_text_tool_batch_result(raw)
    entry = {
        "task_id": task_id,
        "prompt": prompt,
        "model": LLM_MODEL,
        "wall_ms": round(wall_ms, 1),
        "prompt_tokens_est": estimate_messages_tokens(messages),
        "raw_response_preview": raw[:2000],
        "parse": parsed.to_feedback_dict(),
        "tool_names": [(tc.get("function") or {}).get("name") for tc in parsed.tool_calls],
        "success": bool(parsed.tool_calls),
    }
    _log(entry)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=int, default=0, help="Run single task 1-5")
    parser.add_argument("--url", default="https://www.kaggle.com/code/codekey/testing-ol/edit")
    args = parser.parse_args()

    if not _has_api_key():
        print("SKIP: no Cerebras API key configured")
        return 0

    ids = [args.task] if args.task else list(TASKS.keys())
    results = []
    for tid in ids:
        print(f"Running live task {tid}...")
        try:
            results.append(run_task(tid, args.url))
            time.sleep(13)  # respect spacing throttle between live calls
        except Exception as exc:
            err = {"task_id": tid, "success": False, "error": str(exc)}
            _log(err)
            results.append(err)

    summary = {
        "tasks_run": len(results),
        "parse_success": sum(1 for r in results if r.get("success")),
        "failure_rate": 1 - (sum(1 for r in results if r.get("success")) / max(1, len(results))),
        "results": results,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
