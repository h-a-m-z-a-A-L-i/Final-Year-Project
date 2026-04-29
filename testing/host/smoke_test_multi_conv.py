import os
import json
import argparse
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from cerebras.cloud.sdk import Cerebras

CONVERSATIONS_DIR = Path(__file__).with_name("conversations")
RATE_LIMIT_TRACKER = Path(__file__).with_name("rate_limit_tracker.json")

# Rate limits (Free tier)
TPM_LIMIT = 60000  # tokens per minute
TPH_LIMIT = 1000000  # tokens per hour
TPD_LIMIT = 1000000  # tokens per day
RPM_LIMIT = 30  # requests per minute
RPH_LIMIT = 900  # requests per hour
RPD_LIMIT = 14400  # requests per day

client = Cerebras(api_key="csk-ewkx5h936d82wx33hej36cv22nymk5x9f95dhxxk62c9h2yc")


def _parse_args():
    parser = argparse.ArgumentParser(description="Multi-conversation Cerebras chat with rate-limit tracking")
    parser.add_argument("--prompt", default=None, help="Optional first prompt")
    parser.add_argument("--new", action="store_true", help="Start a new conversation")
    parser.add_argument("--list", action="store_true", help="List all conversations")
    parser.add_argument("--conv-id", default=None, help="Switch to conversation by ID")
    return parser.parse_args()


def _ensure_conversations_dir():
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def _list_conversations():
    _ensure_conversations_dir()
    convs = sorted([d for d in CONVERSATIONS_DIR.iterdir() if d.is_dir()])
    if not convs:
        print("No conversations found.")
        return None
    for i, conv in enumerate(convs, 1):
        history_file = conv / "conversation_history.json"
        if history_file.exists():
            history = json.loads(history_file.read_text(encoding="utf-8"))
            msg_count = len(history)
            print(f"{i}. {conv.name} ({msg_count} messages)")
    return convs


def _create_conversation():
    _ensure_conversations_dir()
    conv_id = f"conv_{uuid.uuid4().hex[:8]}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    conv_dir = CONVERSATIONS_DIR / conv_id
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "conversation_history.json").write_text("[]", encoding="utf-8")
    (conv_dir / "token_tracker.json").write_text("{}", encoding="utf-8")
    return conv_id


def _get_current_conv_id():
    marker = CONVERSATIONS_DIR / ".current"
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip()
    convs = _list_conversations()
    if convs:
        return convs[-1].name
    return None


def _set_current_conv_id(conv_id: str):
    _ensure_conversations_dir()
    (CONVERSATIONS_DIR / ".current").write_text(conv_id, encoding="utf-8")


def _load_history(conv_id: str):
    path = CONVERSATIONS_DIR / conv_id / "conversation_history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(conv_id: str, history):
    (CONVERSATIONS_DIR / conv_id / "conversation_history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def _load_rate_tracker():
    if not RATE_LIMIT_TRACKER.exists():
        return {"requests": []}
    try:
        return json.loads(RATE_LIMIT_TRACKER.read_text(encoding="utf-8"))
    except Exception:
        return {"requests": []}


def _save_rate_tracker(data):
    RATE_LIMIT_TRACKER.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _update_rate_tracker(tokens: int, requests: int = 1):
    now = datetime.now(timezone.utc).isoformat()
    tracker = _load_rate_tracker()
    tracker["requests"].append({
        "timestamp": now,
        "tokens": tokens,
        "requests": requests,
    })
    _save_rate_tracker(tracker)


def _check_rate_limits():
    tracker = _load_rate_tracker()
    requests_list = tracker.get("requests", [])
    if not requests_list:
        return True, ""

    now = datetime.now(timezone.utc)
    now_minus_1m = now - timedelta(minutes=1)
    now_minus_1h = now - timedelta(hours=1)
    now_minus_24h = now - timedelta(hours=24)

    tpm, rpm = 0, 0
    tph, rph = 0, 0
    tpd, rpd = 0, 0

    for req in requests_list:
        ts = datetime.fromisoformat(req["timestamp"])
        tokens = req.get("tokens", 0)
        req_count = req.get("requests", 1)

        if ts >= now_minus_1m:
            tpm += tokens
            rpm += req_count
        if ts >= now_minus_1h:
            tph += tokens
            rph += req_count
        if ts >= now_minus_24h:
            tpd += tokens
            rpd += req_count

    violations = []
    if tpm >= TPM_LIMIT:
        violations.append(f"TPM limit ({tpm}/{TPM_LIMIT})")
    if rpm >= RPM_LIMIT:
        violations.append(f"RPM limit ({rpm}/{RPM_LIMIT})")
    if tph >= TPH_LIMIT:
        violations.append(f"TPH limit ({tph}/{TPH_LIMIT})")
    if rph >= RPH_LIMIT:
        violations.append(f"RPH limit ({rph}/{RPH_LIMIT})")
    if tpd >= TPD_LIMIT:
        violations.append(f"TPD limit ({tpd}/{TPD_LIMIT})")
    if rpd >= RPD_LIMIT:
        violations.append(f"RPD limit ({rpd}/{RPD_LIMIT})")

    if violations:
        return False, " | ".join(violations)

    warnings = []
    if tpm > TPM_LIMIT * 0.8:
        warnings.append(f"TPM {tpm}/{TPM_LIMIT} (80%)")
    if rpm > RPM_LIMIT * 0.8:
        warnings.append(f"RPM {rpm}/{RPM_LIMIT} (80%)")
    if tph > TPH_LIMIT * 0.8:
        warnings.append(f"TPH {tph}/{TPH_LIMIT} (80%)")
    if rph > RPH_LIMIT * 0.8:
        warnings.append(f"RPH {rph}/{RPH_LIMIT} (80%)")
    if tpd > TPD_LIMIT * 0.8:
        warnings.append(f"TPD {tpd}/{TPD_LIMIT} (80%)")
    if rpd > RPD_LIMIT * 0.8:
        warnings.append(f"RPD {rpd}/{RPD_LIMIT} (80%)")

    if warnings:
        return True, "[WARN] " + " | ".join(warnings)
    return True, ""


def _ask_once(conv_id: str, prompt_text: str):
    ok, msg = _check_rate_limits()
    if not ok:
        print(f"[RATE LIMIT] {msg}")
        return
    if msg:
        print(msg)

    history = _load_history(conv_id)
    history.append({"role": "user", "content": prompt_text})

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
                print(f"Assistant: Queue busy. Retrying in 1.5s...")
                time.sleep(1.5)
                continue
            else:
                print(f"Assistant: Request failed. ({err})")
                return

    clean_text = _extract_assistant_text(chat_completion)
    if clean_text:
        print(f"Assistant: {clean_text}")
        history.append({"role": "assistant", "content": clean_text})
    else:
        print("Assistant: No content extracted.")

    _save_history(conv_id, history)
    p_toks, c_toks, t_toks = _read_usage(chat_completion)
    _update_rate_tracker(t_toks, 1)


def main():
    args = _parse_args()
    _ensure_conversations_dir()

    if args.list:
        _list_conversations()
        return

    if args.new:
        conv_id = _create_conversation()
        _set_current_conv_id(conv_id)
        print(f"Created new conversation: {conv_id}")
    elif args.conv_id:
        _set_current_conv_id(args.conv_id)
        conv_id = args.conv_id
        print(f"Switched to conversation: {conv_id}")
    else:
        conv_id = _get_current_conv_id()
        if not conv_id:
            conv_id = _create_conversation()
            _set_current_conv_id(conv_id)
            print(f"Created new conversation: {conv_id}")

    print(f"Active conversation: {conv_id}")
    print("Interactive chat started. Press Ctrl+C to stop.")

    first_prompt = str(args.prompt).strip() if args.prompt else "hi"
    if first_prompt and first_prompt != "hi":
        print(f"You: {first_prompt}")
        _ask_once(conv_id, first_prompt)

    while True:
        typed = input("You: ").strip()
        if not typed:
            continue
        _ask_once(conv_id, typed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
