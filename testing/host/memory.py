import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from .config import CHAT_MEMORY_DB, DB_TIMEOUT_SECONDS, MAX_HISTORY_MESSAGES, MAX_PROFILE_FACTS


class LocalMemoryStore:
    """Handles persistent SQLite chat history per notebook."""
    def __init__(self, db_path: Path = CHAT_MEMORY_DB):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=DB_TIMEOUT_SECONDS)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._connect() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, notebook_url TEXT, session_id TEXT NOT NULL DEFAULT 'default', role TEXT, content TEXT, timestamp TEXT)")
                conn.execute("CREATE TABLE IF NOT EXISTS profile_facts (notebook_url TEXT NOT NULL, fact_key TEXT NOT NULL, fact_value TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(notebook_url, fact_key))")
                # Backward-compatible migration for existing databases.
                existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
                if "session_id" not in existing_cols:
                    conn.execute("ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
                conn.execute("UPDATE messages SET session_id = 'default' WHERE session_id IS NULL OR TRIM(session_id) = ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_notebook_url_id ON messages(notebook_url, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_url_session_id ON messages(notebook_url, session_id, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_profile_facts_notebook_url ON profile_facts(notebook_url)")
                conn.commit()

    def append(self, url, role, content, session_id="default"):
        sid = str(session_id or "default")
        with self._lock:
            with self._connect() as conn:
                conn.execute("INSERT INTO messages (notebook_url, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                             (url, sid, role, content, datetime.now(timezone.utc).isoformat()))
                conn.commit()

    def get_history(self, url, limit=MAX_HISTORY_MESSAGES, session_id="default"):
        sid = str(session_id or "default")
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute("SELECT role, content FROM messages WHERE notebook_url = ? AND session_id = ? ORDER BY id DESC LIMIT ?", (url, sid, limit))
                rows = cursor.fetchall()
                return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def list_sessions(self, url, limit=30):
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT session_id, COUNT(*) as message_count, MAX(id) as last_id
                    FROM messages
                    WHERE notebook_url = ?
                    GROUP BY session_id
                    ORDER BY last_id DESC
                    LIMIT ?
                    """,
                    (url, limit),
                ).fetchall()
                return [
                    {
                        "sessionId": r[0],
                        "messageCount": int(r[1] or 0),
                        "lastId": int(r[2] or 0),
                    }
                    for r in rows
                ]

    def clear_history(self, url, session_id=None):
        with self._lock:
            with self._connect() as conn:
                if session_id:
                    conn.execute("DELETE FROM messages WHERE notebook_url = ? AND session_id = ?", (url, str(session_id)))
                else:
                    conn.execute("DELETE FROM messages WHERE notebook_url = ?", (url,))
                conn.commit()

    def upsert_fact(self, url, key, value):
        u = str(url or "").strip()
        k = str(key or "").strip()
        v = str(value or "").strip()
        if not u or not k or not v:
            return
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO profile_facts (notebook_url, fact_key, fact_value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(notebook_url, fact_key)
                    DO UPDATE SET fact_value = excluded.fact_value, updated_at = excluded.updated_at
                    """,
                    (u, k, v, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()

    def get_facts(self, url, limit=MAX_PROFILE_FACTS):
        u = str(url or "").strip()
        if not u:
            return {}
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT fact_key, fact_value
                    FROM profile_facts
                    WHERE notebook_url = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (u, int(limit)),
                ).fetchall()
                out = {}
                for k, v in rows:
                    if k and v and k not in out:
                        out[str(k)] = str(v)
                return out


memory_store = LocalMemoryStore()
