import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host.memory import LocalMemoryStore
from testing.host import notebook_identity as ni


def test_consolidate_chat_history_keys_migrates_registry_urls(tmp_path):
    ni.NOTEBOOK_REGISTRY_PATH = tmp_path / "notebook_registry.json"
    ni.NOTEBOOK_REGISTRY_PATH.write_text(
        """
{
  "url_to_key": {
    "https://www.kaggle.com/code/alice/old-slug/edit": "kaggle:kernel:4242"
  },
  "kernels": {
    "kaggle:kernel:4242": {
      "notebookId": 4242,
      "urls": ["https://www.kaggle.com/code/alice/old-slug/edit"]
    }
  }
}
""",
        encoding="utf-8",
    )
    store = LocalMemoryStore(tmp_path / "chat.sqlite3")
    url = "https://www.kaggle.com/code/alice/old-slug/edit"
    store.append(url, "user", "persisted", session_id="sess-1")

    moved = ni.consolidate_chat_history_keys(store)
    assert moved >= 1
    assert store.get_history("kaggle:kernel:4242", session_id="sess-1")[0]["content"] == "persisted"
    assert store.get_history(url, session_id="sess-1") == []
