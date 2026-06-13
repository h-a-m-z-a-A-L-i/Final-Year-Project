import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.memory import LocalMemoryStore, format_session_title


def test_profile_facts_isolated_by_session(tmp_path):
    db = tmp_path / "chat.sqlite3"
    store = LocalMemoryStore(db_path=db)
    url = "https://example.com/notebook/edit"

    store.upsert_fact(url, "name", "Alice", session_id="sess-a")
    store.upsert_fact(url, "name", "Bob", session_id="sess-b")

    assert store.get_facts(url, session_id="sess-a") == {"name": "Alice"}
    assert store.get_facts(url, session_id="sess-b") == {"name": "Bob"}
    assert store.get_facts(url, session_id="sess-c") == {}


def test_clear_history_clears_session_facts_only(tmp_path):
    db = tmp_path / "chat.sqlite3"
    store = LocalMemoryStore(db_path=db)
    url = "https://example.com/notebook/edit"

    store.upsert_fact(url, "name", "Alice", session_id="sess-a")
    store.upsert_fact(url, "name", "Bob", session_id="sess-b")
    store.append(url, "user", "hi", session_id="sess-a")

    store.clear_history(url, session_id="sess-a")

    assert store.get_facts(url, session_id="sess-a") == {}
    assert store.get_facts(url, session_id="sess-b") == {"name": "Bob"}
    assert store.get_history(url, session_id="sess-a") == []


def test_format_session_title():
    assert format_session_title("  explain cell 4  ") == "explain cell 4"
    assert format_session_title("") == "New conversation"
    long = "a" * 80
    titled = format_session_title(long)
    assert titled.endswith("…")
    assert len(titled) <= 52


def test_list_sessions_uses_first_user_prompt_as_title(tmp_path):
    db = tmp_path / "chat.sqlite3"
    store = LocalMemoryStore(db_path=db)
    url = "https://example.com/notebook/edit"

    store.append(url, "user", "can you explain cell 4", session_id="sess-a")
    store.append(url, "assistant", "Cell 4 drops columns.", session_id="sess-a")
    store.append(url, "user", "what about cell 7?", session_id="sess-b")

    sessions = store.list_sessions(url)
    by_id = {s["sessionId"]: s for s in sessions}
    assert by_id["sess-a"]["title"] == "can you explain cell 4"
    assert by_id["sess-b"]["title"] == "what about cell 7?"


def test_list_sessions_excludes_cell_debug_sessions(tmp_path):
    db = tmp_path / "chat.sqlite3"
    store = LocalMemoryStore(db_path=db)
    url = "https://example.com/notebook/edit"

    store.append(url, "user", "main chat", session_id="main-sess")
    store.append(url, "user", "cell 3 debug", session_id="cell-debug-cell-3")
    store.append(url, "user", "cell 7 debug", session_id="cell-debug-cell-7")

    sessions = store.list_sessions(url)
    ids = {s["sessionId"] for s in sessions}
    assert ids == {"main-sess"}


def test_cell_debug_sessions_isolated_per_cell(tmp_path):
    db = tmp_path / "chat.sqlite3"
    store = LocalMemoryStore(db_path=db)
    url = "https://example.com/notebook/edit"

    store.append(url, "user", "fix cell 3", session_id="cell-debug-cell-3")
    store.append(url, "user", "generate cell 7", session_id="cell-debug-cell-7")

    assert store.get_history(url, session_id="cell-debug-cell-3")[0]["content"] == "fix cell 3"
    assert store.get_history(url, session_id="cell-debug-cell-7")[0]["content"] == "generate cell 7"
    assert store.get_history(url, session_id="cell-debug-cell-3") != store.get_history(
        url, session_id="cell-debug-cell-7"
    )


def test_messages_remain_session_scoped(tmp_path):
    db = tmp_path / "chat.sqlite3"
    store = LocalMemoryStore(db_path=db)
    url = "https://example.com/notebook/edit"

    store.append(url, "user", "hello a", session_id="sess-a")
    store.append(url, "user", "hello b", session_id="sess-b")

    assert len(store.get_history(url, session_id="sess-a")) == 1
    assert store.get_history(url, session_id="sess-a")[0]["content"] == "hello a"
    assert store.get_history(url, session_id="sess-b")[0]["content"] == "hello b"
