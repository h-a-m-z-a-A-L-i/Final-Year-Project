import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from .config import CHAT_MEMORY_DB, DB_TIMEOUT_SECONDS, MAX_HISTORY_MESSAGES, MAX_PROFILE_FACTS

SESSION_TITLE_MAX_CHARS = 52


def format_session_title(first_prompt: str, *, max_chars: int = SESSION_TITLE_MAX_CHARS) -> str:
    """Derive a short conversation label from the first user message."""
    text = " ".join(str(first_prompt or "").split()).strip()
    if not text:
        return "New conversation"
    if len(text) <= max_chars:
        return text
    trimmed = text[: max_chars - 1].rstrip()
    return f"{trimmed}…"


class LocalMemoryStore:
    """SQLite chat memory at CHAT_MEMORY_DB (see config).

    messages: per-notebook, per-session turns (role user|assistant, content, timestamp).
    profile_facts: per-notebook, per-session key/value facts (e.g. user name).

    UI/history loads up to MAX_HISTORY_MESSAGES per session (full text in DB).
    API prompts use trim_history_for_api() in context_budget (fewer/shorter turns).
    Stop/cancel does not append a partial assistant reply (host checks stopped flag).
    """
    def __init__(self, db_path: Path = CHAT_MEMORY_DB):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=DB_TIMEOUT_SECONDS)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _migrate_profile_facts_session_scope(self, conn: sqlite3.Connection) -> None:
        """One-time migration: profile_facts keyed by (notebook_url, fact_key) → add session_id."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(profile_facts)").fetchall()}
        if "session_id" in cols:
            return
        conn.execute(
            """
            CREATE TABLE profile_facts_new (
                notebook_url TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT 'default',
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(notebook_url, session_id, fact_key)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO profile_facts_new (notebook_url, session_id, fact_key, fact_value, updated_at)
            SELECT notebook_url, 'default', fact_key, fact_value, updated_at
            FROM profile_facts
            """
        )
        conn.execute("DROP TABLE profile_facts")
        conn.execute("ALTER TABLE profile_facts_new RENAME TO profile_facts")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_profile_facts_url_session ON profile_facts(notebook_url, session_id)"
        )

    def _ensure_schema(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS messages ("
                    "id INTEGER PRIMARY KEY, notebook_url TEXT, session_id TEXT NOT NULL DEFAULT 'default', "
                    "role TEXT, content TEXT, timestamp TEXT)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS profile_facts ("
                    "notebook_url TEXT NOT NULL, fact_key TEXT NOT NULL, fact_value TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL, PRIMARY KEY(notebook_url, fact_key))"
                )
                existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
                if "session_id" not in existing_cols:
                    conn.execute("ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
                conn.execute("UPDATE messages SET session_id = 'default' WHERE session_id IS NULL OR TRIM(session_id) = ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_notebook_url_id ON messages(notebook_url, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_url_session_id ON messages(notebook_url, session_id, id)")
                self._migrate_profile_facts_session_scope(conn)
                conn.commit()

    def append(self, url, role, content, session_id="default"):
        sid = str(session_id or "default")
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO messages (notebook_url, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (url, sid, role, content, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()

    def get_history(self, url, limit=MAX_HISTORY_MESSAGES, session_id="default"):
        sid = str(session_id or "default")
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT role, content FROM messages WHERE notebook_url = ? AND session_id = ? ORDER BY id DESC LIMIT ?",
                    (url, sid, limit),
                )
                rows = cursor.fetchall()
                return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def list_sessions(self, url, limit=30):
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        m.session_id,
                        COUNT(*) AS message_count,
                        MAX(m.id) AS last_id,
                        (
                            SELECT m2.content
                            FROM messages m2
                            WHERE m2.notebook_url = m.notebook_url
                              AND m2.session_id = m.session_id
                              AND m2.role = 'user'
                            ORDER BY m2.id ASC
                            LIMIT 1
                        ) AS first_prompt
                    FROM messages m
                    WHERE m.notebook_url = ?
                      AND m.session_id NOT LIKE 'cell-debug-%'
                    GROUP BY m.session_id
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
                        "title": format_session_title(r[3] or ""),
                    }
                    for r in rows
                ]

    def clear_history(self, url, session_id=None):
        with self._lock:
            with self._connect() as conn:
                if session_id:
                    sid = str(session_id)
                    conn.execute("DELETE FROM messages WHERE notebook_url = ? AND session_id = ?", (url, sid))
                    conn.execute("DELETE FROM profile_facts WHERE notebook_url = ? AND session_id = ?", (url, sid))
                else:
                    conn.execute("DELETE FROM messages WHERE notebook_url = ?", (url,))
                    conn.execute("DELETE FROM profile_facts WHERE notebook_url = ?", (url,))
                conn.commit()

    def migrate_notebook_key(self, old_key: str, new_key: str) -> int:
        """Move chat rows from one notebook key to another (e.g. URL slug -> stable kernel id)."""
        old = str(old_key or "").strip()
        new = str(new_key or "").strip()
        if not old or not new or old == new:
            return 0
        moved = 0
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM messages WHERE notebook_url = ?", (old,))
                moved += int(cur.fetchone()[0] or 0)
                conn.execute(
                    "UPDATE messages SET notebook_url = ? WHERE notebook_url = ?",
                    (new, old),
                )
                conn.execute(
                    "UPDATE profile_facts SET notebook_url = ? WHERE notebook_url = ?",
                    (new, old),
                )
                conn.commit()
        return moved

    def upsert_fact(self, url, key, value, session_id="default"):
        u = str(url or "").strip()
        k = str(key or "").strip()
        v = str(value or "").strip()
        sid = str(session_id or "default")
        if not u or not k or not v:
            return
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO profile_facts (notebook_url, session_id, fact_key, fact_value, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(notebook_url, session_id, fact_key)
                    DO UPDATE SET fact_value = excluded.fact_value, updated_at = excluded.updated_at
                    """,
                    (u, sid, k, v, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()

    def get_facts(self, url, session_id="default", limit=MAX_PROFILE_FACTS):
        u = str(url or "").strip()
        sid = str(session_id or "default")
        if not u:
            return {}
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT fact_key, fact_value
                    FROM profile_facts
                    WHERE notebook_url = ? AND session_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (u, sid, int(limit)),
                ).fetchall()
                out = {}
                for k, v in rows:
                    if k and v and k not in out:
                        out[str(k)] = str(v)
                return out


memory_store = LocalMemoryStore()
