import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.memory import LocalMemoryStore
from testing.host import notebook_identity as ni


def test_stable_notebook_key():
    assert ni.stable_notebook_key(56005324) == "kaggle:kernel:56005324"
    assert ni.stable_notebook_key(None) is None
    assert ni.stable_notebook_key("bad") is None


def test_register_migrates_chat_from_old_url_to_stable_key(tmp_path):
    ni.NOTEBOOK_REGISTRY_PATH = tmp_path / "notebook_registry.json"
    store = LocalMemoryStore(tmp_path / "chat.sqlite3")
    old_url = "https://www.kaggle.com/code/alice/old-slug/edit"
    new_url = "https://www.kaggle.com/code/alice/new-slug/edit"
    store.append(old_url, "user", "hello", session_id="default")
    store.append(old_url, "assistant", "hi", session_id="default")

    info = ni.register_notebook_identity(
        new_url,
        4242,
        memory_store=store,
        old_url=old_url,
    )
    assert info["notebookKey"] == "kaggle:kernel:4242"
    assert info["migratedRows"] >= 2

    history = store.get_history("kaggle:kernel:4242", session_id="default")
    assert len(history) == 2
    assert history[0]["content"] == "hello"
    assert store.get_history(old_url, session_id="default") == []


def test_resolve_history_key_uses_registry_after_url_change(tmp_path):
    ni.NOTEBOOK_REGISTRY_PATH = tmp_path / "notebook_registry.json"
    store = LocalMemoryStore(tmp_path / "chat.sqlite3")
    old_url = "https://www.kaggle.com/code/alice/my-notebook/edit"
    renamed_url = "https://www.kaggle.com/code/alice/renamed-notebook/edit"
    ni.register_notebook_identity(old_url, 99, memory_store=store)
    ni.handle_notebook_url_changed(old_url, renamed_url, 99, memory_store=store)
    resolved = ni.resolve_history_key(
        renamed_url,
        None,
        memory_store=store,
    )
    assert resolved == "kaggle:kernel:99"


def test_url_change_without_id_reuses_mapped_key(tmp_path):
    ni.NOTEBOOK_REGISTRY_PATH = tmp_path / "notebook_registry.json"
    store = LocalMemoryStore(tmp_path / "chat.sqlite3")
    old_url = "https://www.kaggle.com/code/alice/old/edit"
    new_url = "https://www.kaggle.com/code/alice/new/edit"
    ni.register_notebook_identity(old_url, 77, memory_store=store)
    store.append("kaggle:kernel:77", "user", "persist", session_id="default")

    info = ni.handle_notebook_url_changed(
        old_url,
        new_url,
        77,
        memory_store=store,
    )
    assert info["notebookKey"] == "kaggle:kernel:77"
    history = store.get_history("kaggle:kernel:77", session_id="default")
    assert len(history) == 1
