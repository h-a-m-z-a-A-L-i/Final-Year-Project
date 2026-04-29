import os
import json
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from cerebras.cloud.sdk import Cerebras

TRACKER_PATH = Path(__file__).with_name("token_tracker.txt")
HISTORY_PATH = Path(__file__).with_name("conversation_history.json")
USER_PROMPT = os.environ.get("SMOKE_PROMPT", "hi")

client = Cerebras(api_key="csk-ewkx5h936d82wx33hej36cv22nymk5x9f95dhxxk62c9h2yc")


def _parse_args():
    parser = argparse.ArgumentParser(description="Cerebras smoke chat with persistent history")
    parser.add_argument("--prompt", default=None, help="Optional first prompt before interactive loop")
    return parser.parse_args()

def _load_history(path: Path):
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role in {"system", "user", "assistant"} and content:
            out.append({"role": role, "content": content})
    return out


def _save_history(path: Path, history):
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

def _extract_assistant_text(resp) -> str:
    try:
        choices = getattr(resp, "choices", None) or []
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg is not None:
                content = getattr(msg, "content", "")
                if isinstance(content, str):
                    return content.strip()
                return str(content).strip()
    except Exception:
        pass
    return ""


def _read_usage(resp):
    usage = getattr(resp, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    return prompt_tokens, completion_tokens, total_tokens


def _load_totals(path: Path):
    if not path.exists():
        return 0, 0, 0
    prompt_total = completion_total = all_total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("TOTAL_PROMPT_TOKENS="):
            prompt_total = int(line.split("=", 1)[1].strip() or 0)
        elif line.startswith("TOTAL_COMPLETION_TOKENS="):
            completion_total = int(line.split("=", 1)[1].strip() or 0)
        elif line.startswith("TOTAL_TOKENS="):
            all_total = int(line.split("=", 1)[1].strip() or 0)
    return prompt_total, completion_total, all_total


def _update_tracker(path: Path, prompt_t: int, completion_t: int, total_t: int):
    prev_prompt, prev_completion, prev_total = _load_totals(path)
    new_prompt = prev_prompt + prompt_t
    new_completion = prev_completion + completion_t
    new_total = prev_total + total_t

    prev_lines = []
    if path.exists():
        prev_lines = path.read_text(encoding="utf-8").splitlines()
    history_lines = [
        ln for ln in prev_lines
        if not ln.startswith("TOTAL_PROMPT_TOKENS=")
        and not ln.startswith("TOTAL_COMPLETION_TOKENS=")
        and not ln.startswith("TOTAL_TOKENS=")
        and ln != "RUN_HISTORY:"
        and ln.strip() != ""
    ]

    now = datetime.now(timezone.utc).isoformat()
    history_lines.append(
        f"{now} | prompt={prompt_t} completion={completion_t} total={total_t}"
    )

    out = [
        f"TOTAL_PROMPT_TOKENS={new_prompt}",
        f"TOTAL_COMPLETION_TOKENS={new_completion}",
        f"TOTAL_TOKENS={new_total}",
        "",
        "RUN_HISTORY:",
        *history_lines,
    ]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _ask_once(prompt_text: str):
    history = _load_history(HISTORY_PATH)
    history.append({"role": "user", "content": prompt_text})
    # Retry on 429 / queue_exceeded until we get a valid response.
    while True:
        try:
            chat_completion = client.chat.completions.create(
                messages=history,
                model="qwen-3-235b-a22b-instruct-2507",
            )
            break
        except Exception as ex:
            err = str(ex)
            if "429" in err or "queue_exceeded" in err or "too_many_requests_error" in err:
                print(f"Assistant: Queue is busy right now. Retrying in 1.5s... ({err})")
                time.sleep(1.5)
                continue
            else:
                print(f"Assistant: Request failed, but chat is still running. ({err})")
                return

    clean_text = _extract_assistant_text(chat_completion)
    if clean_text:
        print(f"Assistant: {clean_text}")
        history.append({"role": "assistant", "content": clean_text})
    else:
        print("Assistant: No assistant content extracted.")

    _save_history(HISTORY_PATH, history)

    p_toks, c_toks, t_toks = _read_usage(chat_completion)
    _update_tracker(TRACKER_PATH, p_toks, c_toks, t_toks)


def main():
    args = _parse_args()
    first_prompt = str(args.prompt).strip() if args.prompt else USER_PROMPT.strip()

    print("Interactive chat started. Press Ctrl+C to stop.")

    # Send one initial prompt (CLI prompt if provided, otherwise env/default), then keep asking.
    if first_prompt:
        print(f"You: {first_prompt}")
        _ask_once(first_prompt)

    while True:
        typed = input("You: ").strip()
        if not typed:
            continue
        _ask_once(typed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")