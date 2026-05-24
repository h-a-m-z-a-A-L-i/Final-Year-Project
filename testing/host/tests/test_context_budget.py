import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.context_budget import (
    estimate_messages_tokens,
    fit_messages_to_budget,
    trim_history_for_api,
)


def test_trim_history_for_api_caps_count_and_chars():
    history = [{"role": "user", "content": "x" * 5000} for _ in range(20)]
    trimmed = trim_history_for_api(history)
    assert len(trimmed) <= 10
    assert all(len(m["content"]) <= 1000 + 50 for m in trimmed)


def test_fit_messages_drops_old_turns():
    messages = [{"role": "system", "content": "sys " * 100}]
    for i in range(15):
        messages.append({"role": "user", "content": f"old {i} " * 200})
        messages.append({"role": "assistant", "content": f"reply {i} " * 200})
    messages.append({"role": "user", "content": "current question"})
    fitted = fit_messages_to_budget(messages, max_tokens=800)
    assert fitted[-1]["content"] == "current question"
    assert estimate_messages_tokens(fitted) <= 800
