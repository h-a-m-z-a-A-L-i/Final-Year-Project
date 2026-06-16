import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.memory import LocalMemoryStore
from testing.host import notebook_identity as ni


def test_resolve_history_key_uses_kaggle_cache(tmp_path, monkeypatch):
    ni.NOTEBOOK_REGISTRY_PATH = tmp_path / "notebook_registry.json"
    store = LocalMemoryStore(tmp_path / "chat.sqlite3")

    monkeypatch.setattr(
        "testing.host.kaggle_kernel_client.resolve_kernel_id_for_url",
        lambda url, log=None, allow_fetch=True: 112732919,
    )

    url = "https://www.kaggle.com/code/codekey/testing-onlll/edit"
    store.append(url, "user", "hello", session_id="default")
    key = ni.resolve_history_key(url, memory_store=store)
    assert key == "kaggle:kernel:112732919"
    assert store.get_history("kaggle:kernel:112732919", session_id="default")[0]["content"] == "hello"
