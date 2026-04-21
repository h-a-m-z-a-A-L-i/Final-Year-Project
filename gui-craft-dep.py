#!/usr/bin/env python3
"""
GUI Craft – Isolated Design & Testing Environment
Serves a local sandbox for designing the injected GUI and proxies chat requests.
"""

import os
import sys
import time
import json
import requests
import threading
import re
import sqlite3
import uuid

# Set stdout to UTF-8 for compatibility with some Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from typing import Optional, Dict, Set, List
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ========== DEPENDENCY MODULE IMPORT ==========
# Locate the dependency_mode folder and add it to sys.path once
_DEP_MODE_DIR = str(Path(__file__).resolve().parents[2] / 'DB' / 'dependency_mode')
if _DEP_MODE_DIR not in sys.path:
    sys.path.insert(0, _DEP_MODE_DIR)

try:
    from dependency_tracker import DependencyTracker
    from context_builder import ContextBuilder
    _DEP_AVAILABLE = True
except ImportError as _e:
    print(f"[DEP] Warning: Could not import dependency modules ({_e}). Dependency graph disabled.")
    _DEP_AVAILABLE = False


def _sanitize_url_to_filename(url: str) -> str:
    """Convert a notebook URL to the JSON filename saved by scrapper.py."""
    name = _normalize_notebook_url(url)
    if not name:
        name = "unknown_notebook"
    name = name.replace('https://', '').replace('http://', '')
    name = re.sub(r'[\/\\:?*<>|"]', '_', name)
    return name if name.endswith('.json') else name + '.json'


def _normalize_notebook_url(url: str) -> str:
    """Return a stable notebook identity URL so history keys do not drift across routes."""
    raw = (url or "").strip()
    if not raw:
        return ""

    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            cleaned = raw.split('#', 1)[0].split('?', 1)[0].rstrip('/')
            return cleaned

        path = (parsed.path or '/').rstrip('/') or '/'
        parts = [p for p in path.split('/') if p]

        # Kaggle notebook pages are identity-stable on /code/<owner>/<slug>.
        if len(parts) >= 3 and parts[0].lower() == 'code':
            path = '/' + '/'.join(parts[:3])

        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, '', '', ''))
    except Exception:
        return raw.split('#', 1)[0].split('?', 1)[0].rstrip('/')


class DependencyManager:
    """
    Manages isolated DependencyTracker + ContextBuilder instances for each
    notebook.  Each instance is keyed by the notebook URL (via its sanitized
    JSON filename) and is automatically reloaded when the file on disk changes.
    """

    def __init__(self, json_dir: Optional[str] = None):
        # Default: look in cwd — where scrapper.py saves the JSON files
        self.json_dir = Path(json_dir) if json_dir else Path.cwd()
        self._cache: Dict[str, dict] = {}  # filename -> {builder, mtime}

    def get_builder(self, notebook_url: str):
        """Return ContextBuilder for notebook_url, loading or refreshing as needed."""
        if not _DEP_AVAILABLE or not notebook_url:
            return None

        filename = _sanitize_url_to_filename(notebook_url)
        json_path = self.json_dir / filename

        if not json_path.exists():
            print(f"[DEP] JSON not found: {json_path}")
            return None

        current_mtime = json_path.stat().st_mtime
        cached = self._cache.get(filename)

        if cached is None or cached['mtime'] != current_mtime:
            print(f"[DEP] Loading/refreshing graph for: {filename}")
            result = self._build_graph(json_path)
            if result:
                result['mtime'] = current_mtime
                self._cache[filename] = result
                print(f"[DEP] Graph ready: {result['cell_count']} cells loaded.")

        entry = self._cache.get(filename)
        return entry['builder'] if entry else None

    def _build_graph(self, json_path: Path) -> Optional[dict]:
        """Load cells from scrapper JSON and build a DependencyTracker + ContextBuilder."""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                cells = json.load(f)

            tracker = DependencyTracker()
            cells_data: Dict = {}

            for cell in cells:
                # Support both scrapper.py format (cell_number, input) and legacy (index, input_lines)
                cell_id = cell.get('cell_number', cell.get('index', 0))

                inp = cell.get('input', cell.get('input_lines', ''))
                code = ''.join(inp) if isinstance(inp, list) else str(inp)

                out = cell.get('output', '')
                output = ''.join(out) if isinstance(out, list) else str(out)

                cells_data[cell_id] = {'code': code, 'output': output}
                tracker.update_cell(cell_id, code)

            tracker.update_all_reverse_dependencies()
            builder = ContextBuilder(tracker, cells_data)
            return {'builder': builder, 'cell_count': len(cells_data)}

        except Exception as exc:
            print(f"[DEP] Failed to build graph from {json_path}: {exc}")
            return None


# One shared manager — survives across HTTP requests
dep_manager = DependencyManager()

try:
    import websocket
except ImportError:
    print("❌ Missing required library: websocket-client")
    print("   Install it with: pip install websocket-client")
    sys.exit(1)

# ========== CONFIGURATION ==========
DEFAULT_DEBUG_PORT = 9222
DEFAULT_TARGET_SELECTOR = "#site-content > div.sc-dLSJrv.gzwoYT > div > div.sc-dtrpA-d.jByWKC > div > div > div.sc-dUdFYf.qsyOa > button"
PROXY_PORT = 8080
NGROK_URL = "https://palacelike-lainey-unsinged.ngrok-free.dev/generate"
CHAT_MEMORY_DB = Path(__file__).resolve().parent / "chat_memory.sqlite3"
MAX_HISTORY_MESSAGES = 24
ALLOWED_MODES = {"simple", "explain_error", "dependency", "code_review", "explain_code"}
MODE_SYSTEM_PROMPTS = {
    "simple": (
        "You are My Copilot, a practical coding assistant for notebook workflows. "
        "Use prior conversation memory for follow-ups and keep answers concise but complete. "
        "When the user asks for code, return runnable code blocks first, then a short explanation."
    ),
    "explain_error": (
        "You are an expert Python debugger. "
        "Always respond with: 1) Root cause, 2) Exact fix, 3) Corrected code snippet, 4) Prevention checklist. "
        "Do not be vague; mention the failing symbol/line when evidence is available."
    ),
    "dependency": (
        "You are a notebook dependency analyst. "
        "Prioritize provided notebook context and dependency graph. "
        "Explain upstream/downstream impact and give a safest fix order with cell-level actions."
    ),
    "code_review": (
        "You are a strict code reviewer. "
        "Focus on bugs, behavioral regressions, edge cases, security risks, and missing tests. "
        "Return findings first, ordered by severity, then optional improvement notes."
    ),
    "explain_code": (
        "You are a code explainer. "
        "Explain what the code does, why it works, complexity tradeoffs, and failure points. "
        "Use plain language and short examples."
    ),
}

GLOBAL_RESPONSE_STYLE = (
    "General rules: be precise, avoid hallucinations, and prefer explicit steps over generic advice. "
    "If context is insufficient, state assumptions briefly before answering."
)


def _derive_stop_url(generate_url: str) -> str:
    base = (generate_url or "").rstrip('/')
    if base.endswith('/generate'):
        return base[:-len('/generate')] + '/stop'
    return base + '/stop'


NGROK_STOP_URL = _derive_stop_url(NGROK_URL)


def _resolve_mode(prompt: str, requested_mode: str, is_debug: bool) -> str:
    mode = (requested_mode or "").strip().lower()
    if mode in ALLOWED_MODES:
        return mode

    if is_debug:
        return "dependency"

    text = prompt or ""

    if re.search(r"\b(code\s*review|review\s*this|audit\s*this|find\s*bugs|regression|security\s*review)\b", text, re.IGNORECASE):
        return "code_review"

    if re.search(r"\b(explain\s*this\s*code|how\s*does\s*this\s*work|walk\s*me\s*through\s*this\s*code|understand\s*this\s*code)\b", text, re.IGNORECASE):
        return "explain_code"

    if re.search(r"\b(traceback|exception|error|nameerror|typeerror|valueerror|keyerror|indexerror|attributeerror)\b", text, re.IGNORECASE):
        return "explain_error"

    return "simple"


def _get_system_prompt(mode: str, prompt: str = "", has_context: bool = False) -> str:
    key = (mode or "simple").strip().lower()
    base = MODE_SYSTEM_PROMPTS.get(key, MODE_SYSTEM_PROMPTS["simple"])

    dynamic_rules = []
    if has_context:
        dynamic_rules.append("Use the provided notebook context as ground truth when resolving dependencies.")

    if re.search(r"\b(remember|my\s+name|what\s+is\s+my\s+name|previous\s+message|follow\s*up)\b", prompt or "", re.IGNORECASE):
        dynamic_rules.append("This is memory-sensitive: prioritize chat history facts over guessing.")

    if key == "code_review":
        dynamic_rules.append("Format findings as: [Severity] issue -> impact -> fix.")

    parts = [base, GLOBAL_RESPONSE_STYLE] + dynamic_rules
    return " ".join([p for p in parts if p]).strip()


class LocalMemoryStore:
    """Persists per-notebook chat sessions and messages on local disk."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        notebook_key TEXT PRIMARY KEY,
                        notebook_url TEXT,
                        session_id TEXT NOT NULL,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        notebook_key TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        mode TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_notebook_session
                    ON messages(notebook_key, session_id, id)
                    """
                )
                conn.commit()

    def _tab_key(self, tab_id: Optional[str]) -> str:
        raw = (tab_id or "").strip() or "default"
        return re.sub(r'[^A-Za-z0-9_.-]', '_', raw)[:120]

    def _notebook_key(self, notebook_url: str, tab_id: Optional[str] = None) -> str:
        if not notebook_url:
            base = "unknown_notebook.json"
        else:
            base = _sanitize_url_to_filename(notebook_url)
        return f"{base}__tab__{self._tab_key(tab_id)}"

    def resolve_session(self, notebook_url: str, requested_session_id: Optional[str], tab_id: Optional[str] = None) -> str:
        notebook_key = self._notebook_key(notebook_url, tab_id)
        session_id = (requested_session_id or "").strip()

        with self._lock:
            with self._connect() as conn:
                if not session_id:
                    row = conn.execute(
                        "SELECT session_id FROM sessions WHERE notebook_key = ?",
                        (notebook_key,)
                    ).fetchone()
                    if row and row["session_id"]:
                        session_id = row["session_id"]
                    else:
                        session_id = str(uuid.uuid4())

                conn.execute(
                    """
                    INSERT INTO sessions (notebook_key, notebook_url, session_id, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(notebook_key)
                    DO UPDATE SET session_id=excluded.session_id,
                                  notebook_url=excluded.notebook_url,
                                  updated_at=CURRENT_TIMESTAMP
                    """,
                    (notebook_key, notebook_url, session_id)
                )
                conn.commit()

        return session_id

    def rebind_session(self, notebook_url: str, old_session_id: str, new_session_id: str, tab_id: Optional[str] = None):
        old_id = (old_session_id or "").strip()
        new_id = (new_session_id or "").strip()
        if not old_id or not new_id or old_id == new_id:
            return

        notebook_key = self._notebook_key(notebook_url, tab_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE messages
                    SET session_id = ?
                    WHERE notebook_key = ? AND session_id = ?
                    """,
                    (new_id, notebook_key, old_id)
                )
                conn.execute(
                    """
                    INSERT INTO sessions (notebook_key, notebook_url, session_id, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(notebook_key)
                    DO UPDATE SET session_id=excluded.session_id,
                                  notebook_url=excluded.notebook_url,
                                  updated_at=CURRENT_TIMESTAMP
                    """,
                    (notebook_key, notebook_url, new_id)
                )
                conn.commit()

    def append_message(self, notebook_url: str, session_id: str, role: str, content: str, mode: str = "simple", tab_id: Optional[str] = None):
        text = (content or "").strip()
        if not text:
            return

        notebook_key = self._notebook_key(notebook_url, tab_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO messages (notebook_key, session_id, role, content, mode)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (notebook_key, session_id, role, text, mode)
                )
                conn.execute(
                    """
                    INSERT INTO sessions (notebook_key, notebook_url, session_id, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(notebook_key)
                    DO UPDATE SET session_id=excluded.session_id,
                                  notebook_url=excluded.notebook_url,
                                  updated_at=CURRENT_TIMESTAMP
                    """,
                    (notebook_key, notebook_url, session_id)
                )
                conn.commit()

    def get_history(self, notebook_url: str, session_id: str, limit: int = MAX_HISTORY_MESSAGES, tab_id: Optional[str] = None) -> List[dict]:
        notebook_key = self._notebook_key(notebook_url, tab_id)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT role, content
                    FROM messages
                    WHERE notebook_key = ? AND session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (notebook_key, session_id, max(0, int(limit)))
                ).fetchall()

        history = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
        return history

    def trim_history(self, notebook_url: str, session_id: str, keep_last: int = 48, tab_id: Optional[str] = None):
        keep = max(2, int(keep_last))
        notebook_key = self._notebook_key(notebook_url, tab_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    DELETE FROM messages
                    WHERE notebook_key = ? AND session_id = ?
                      AND id NOT IN (
                          SELECT id FROM messages
                          WHERE notebook_key = ? AND session_id = ?
                          ORDER BY id DESC
                          LIMIT ?
                      )
                    """,
                    (notebook_key, session_id, notebook_key, session_id, keep)
                )
                conn.commit()

    def list_conversations(self, notebook_url: str, tab_id: Optional[str] = None, limit: int = 30) -> dict:
        notebook_key = self._notebook_key(notebook_url, tab_id)
        safe_limit = max(1, min(int(limit), 200))

        with self._lock:
            with self._connect() as conn:
                active_row = conn.execute(
                    "SELECT session_id FROM sessions WHERE notebook_key = ?",
                    (notebook_key,)
                ).fetchone()

                rows = conn.execute(
                    """
                    SELECT session_id,
                           MAX(id) AS last_id,
                           MAX(created_at) AS updated_at,
                           COUNT(*) AS message_count
                    FROM messages
                    WHERE notebook_key = ?
                    GROUP BY session_id
                    ORDER BY last_id DESC
                    LIMIT ?
                    """,
                    (notebook_key, safe_limit)
                ).fetchall()

                conversations = []
                for row in rows:
                    sid = row["session_id"]
                    first_user = conn.execute(
                        """
                        SELECT content
                        FROM messages
                        WHERE notebook_key = ? AND session_id = ? AND role = 'user'
                        ORDER BY id ASC
                        LIMIT 1
                        """,
                        (notebook_key, sid)
                    ).fetchone()

                    title = (first_user["content"] if first_user else "New conversation").strip() or "New conversation"
                    if len(title) > 72:
                        title = title[:69] + "..."

                    conversations.append({
                        "session_id": sid,
                        "title": title,
                        "updated_at": row["updated_at"],
                        "message_count": int(row["message_count"] or 0),
                    })

                active_session_id = active_row["session_id"] if active_row else None
                if active_session_id and not any(c["session_id"] == active_session_id for c in conversations):
                    conversations.insert(0, {
                        "session_id": active_session_id,
                        "title": "New conversation",
                        "updated_at": None,
                        "message_count": 0,
                    })

        return {
            "active_session_id": active_session_id,
            "conversations": conversations,
        }


memory_store = LocalMemoryStore(CHAT_MEMORY_DB)



# The HTML/CSS of your panel - Copilot Inspired Layout
DEFAULT_PANEL_CONTENT = """
    <div class="copilot-container">
        <!-- Header -->
        <header class="copilot-header">
            <div class="header-left">
                <span class="header-title">My Copilot</span>
                <button id="history-toggle-btn" class="header-mini-btn" title="Open conversation history">History</button>
            </div>
            <div class="header-actions">
                <button id="history-new" class="header-mini-btn primary" title="Start new conversation">+ New</button>
            </div>
        </header>

        <div id="history-dropdown" class="history-dropdown">
            <div class="history-dropdown-header">
                <span class="history-label">Conversations</span>
            </div>
            <div id="history-list" class="history-scroll-area">
                <p class="history-placeholder">No saved conversations yet.</p>
            </div>
        </div>

        <!-- Tab Bar -->
        <nav class="copilot-tabs">
            <button class="tab-item active" data-tab="chat-tab">💬 Chat</button>
            <button class="tab-item" data-tab="debug-tab">🔗 Dependencies</button>
        </nav>

        <!-- Main Content Area -->
        <main class="copilot-main">
            <div id="chat-tab" class="tab-content active">
                <div id="chat-history" class="chat-scroll-area">
                    <div class="message assistant">
                        <div class="bubble">
                            Hello! I am your AI assistant. How can I help you today?
                        </div>
                    </div>
                </div>
            </div>

            <div id="debug-tab" class="tab-content">
                <div class="debug-toolbar">
                    <span class="debug-label">Dependency Graph</span>
                    <button id="debug-refresh" class="debug-refresh-btn" title="Refresh">↻ Refresh</button>
                </div>
                <div id="debug-content" class="debug-scroll-area">
                    <p class="debug-placeholder">Click ↻ Refresh to load the dependency graph for this notebook.</p>
                </div>
            </div>
        </main>

        <!-- Footer / Input -->
        <footer class="copilot-footer">
            <div class="input-wrapper">
                <textarea id="chat-input" rows="1" placeholder="Ask me anything..." autocomplete="off"></textarea>
                <div class="input-actions">
                    <button id="chat-send" class="send-btn" title="Send message">➔</button>
                    <button id="chat-stop" class="stop-btn" title="Stop generation" style="display:none;">⏹</button>
                </div>
            </div>
            <div class="footer-note"></div>
        </footer>
    </div>
"""
# JavaScript to inject - handles logic, icons, and positioning
JS_INJECTION_TEMPLATE = r"""
(function(content, targetSelector) {{
    if (!document.body) return 'DOM_NOT_READY';

    // Cleanup
    try {{
        const existing = document.getElementById('injected-copilot-panel-wrapper');
        if (existing) existing.remove();
        const existingBtn = document.getElementById('injected-copilot-toggle-btn');
        if (existingBtn) existingBtn.remove();
        const existingStyles = document.getElementById('copilot-injected-styles');
        if (existingStyles) existingStyles.remove();
    }} catch (e) {{}}
       // Inject Styles
    const styleTag = document.createElement('style');
    styleTag.id = 'copilot-injected-styles';
    styleTag.textContent = `
        :root {{
            --cp-bg: #1e1f23;
            --cp-text: #f0f0f0;
            --cp-accent: #47a1ff;
            --cp-accent-hover: #6cb6ff;
            --cp-bubble-user: #47a1ff;
            --cp-bubble-user-text: #ffffff;
            --cp-bubble-bot: #2b2c31;
            --cp-bubble-bot-border: #3f4046;
            --cp-header-bg: #1e1f23;
            --cp-border: #33343a;
            --cp-shadow: rgba(0, 0, 0, 0.3);
        }}

        #injected-copilot-panel-wrapper {{
            position: fixed;
            top: 0;
            right: 0;
            width: min(395px, 100vw);
            height: 100vh;
            background: var(--cp-bg);
            color: var(--cp-text);
            box-shadow: -4px 0 15px var(--cp-shadow);
            z-index: 9999999;
            display: none;
            overflow: hidden;
            flex-direction: column;
            border-left: 1px solid var(--cp-border);
            transition: transform 0.3s ease;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            overflow-y: hidden;
        }}

        #injected-copilot-panel-wrapper.active {{ display: flex; animation: cpSlideIn 0.3s ease; }}
        @keyframes cpSlideIn {{ from {{ transform: translateX(100%); }} to {{ transform: translateX(0); }} }}

        .copilot-container {{
            display: flex;
            flex-direction: column;
            height: 100%;
            position: relative;
        }}

        .copilot-header {{
            height: 40px;
            padding: 0 11px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--cp-header-bg);
            border-bottom: 1px solid var(--cp-border);
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        .header-left {{ display: flex; align-items: center; gap: 10px; }}
        .header-title {{ font-weight: 600; font-size: 14px; }}
        .header-actions {{ display: flex; align-items: center; gap: 6px; }}
        .header-mini-btn {{
            border: 1px solid var(--cp-border);
            background: transparent;
            color: var(--cp-text);
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s;
            opacity: 0.9;
        }}
        .header-mini-btn:hover {{ border-color: var(--cp-accent); opacity: 1; }}
        .header-mini-btn.active {{
            background: rgba(71,161,255,0.15);
            border-color: var(--cp-accent);
            color: var(--cp-accent);
        }}
        .header-mini-btn.primary {{
            background: var(--cp-accent);
            border-color: var(--cp-accent);
            color: #fff;
        }}
        .header-mini-btn.primary:hover {{
            background: var(--cp-accent-hover);
            border-color: var(--cp-accent-hover);
            color: #fff;
        }}

        .copilot-tabs {{
            display: flex;
            padding: 0 11px;
            background: var(--cp-header-bg);
            border-bottom: 1px solid var(--cp-border);
            gap: 16px;
            flex-shrink: 0;
        }}
        .tab-item {{
            padding: 10px 4px;
            font-size: 13px;
            color: var(--cp-text);
            opacity: 0.6;
            cursor: pointer;
            border: none;
            background: none;
            border-bottom: 2px solid transparent;
            font-family: inherit;
            transition: all 0.2s;
        }}
        .tab-item.active {{
            opacity: 1;
            font-weight: 600;
            border-bottom-color: var(--cp-accent);
        }}

        .copilot-main {{
            flex: 1;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            position: relative;
            background: var(--cp-bg);
        }}

        .chat-scroll-area {{
            flex: 1;
            overflow-y: auto;
            padding: 11px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            scroll-behavior: smooth;
            overscroll-behavior: contain;
        }}
        .chat-scroll-area::-webkit-scrollbar {{ width: 5px; }}
        .chat-scroll-area::-webkit-scrollbar-thumb {{ background: var(--cp-border); border-radius: 10px; }}

        .message {{
            display: flex;
            flex-direction: column;
            max-width: 95%;
            width: fit-content;
            margin-bottom: 0px;
        }}
        .message.user {{ align-self: flex-end; align-items: flex-end; margin-left: auto; }}
        .message.assistant {{ align-self: flex-start; align-items: flex-start; margin-right: auto; }}
        
        .bubble {{
            padding: 5px 9px;
            border-radius: 16px;
            font-size: 13.5px;
            line-height: 1.6;
            word-wrap: break-word;
            white-space: pre-wrap;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            max-width: 100%;
        }}
        .user .bubble {{ background: var(--cp-bubble-user); color: var(--cp-bubble-user-text); border-bottom-right-radius: 4px; border: none; }}
        .assistant .bubble {{ background: var(--cp-bubble-bot); border: 1px solid var(--cp-bubble-bot-border); border-bottom-left-radius: 4px; }}

        .code-block-wrapper {{
            position: relative;
            margin: 12px 0;
            border-radius: 8px;
            overflow: hidden;
            background: #282c34;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .code-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: #20232a;
            font-size: 11px;
            color: #abb2bf;
            font-family: sans-serif;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .copy-btn {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: #ffffff;
            padding: 4px 12px;
            border-radius: 20px;
            cursor: pointer;
            font-size: 11px;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            align-items: center;
            gap: 6px;
            line-height: 1;
        }}
        .copy-icon {{
            width: 14px;
            height: 14px;
            display: block;
            flex-shrink: 0;
            stroke: #ffffff;
        }}
        .copy-btn:hover {{
            background: rgba(255, 255, 255, 0.2);
            border-color: rgba(255, 255, 255, 0.4);
            transform: translateY(-1px);
        }}
        .copy-btn.copied {{
            background: #28a745;
            border-color: #28a745;
            color: white;
        }}
        .code-block {{
            background: #282c34;
            color: #abb2bf;
            padding: 16px;
            font-family: "Fira Code", "Consolas", monospace;
            font-size: 13px;
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
            line-height: 1.5;
        }}

        /* Catch ALL pre/code produced by markdown-it inside bubbles */
        .bubble pre {{
            background: #282c34 !important;
            color: #abb2bf !important;
            font-family: "Fira Code", "Consolas", "Courier New", monospace !important;
            font-size: 13px !important;
            padding: 16px !important;
            margin: 0 !important;
            white-space: pre-wrap !important;
            word-wrap: break-word !important;
            line-height: 1.5 !important;
            overflow-x: auto;
        }}
        .bubble code {{
            background: #282c34 !important;
            color: #abb2bf !important;
            font-family: "Fira Code", "Consolas", "Courier New", monospace !important;
            font-size: 0.9em;
            border-radius: 4px;
            padding: 2px 5px;
        }}
        /* Inline code (not inside pre) gets lighter bg */
        .bubble p code, .bubble li code {{
            background: rgba(40,44,52,0.15) !important;
            color: #d63384 !important;
            padding: 1px 5px;
            border-radius: 3px;
        }}

        /* Markdown-it rendered content: headings, lists, paragraphs */
        .bubble h1, .bubble h2, .bubble h3, .bubble h4 {{
            color: var(--cp-accent);
            margin: 14px 0 6px 0;
            font-weight: 700;
            line-height: 1.3;
        }}
        .bubble h1 {{ font-size: 1.4em; border-bottom: 2px solid var(--cp-border); padding-bottom: 4px; }}
        .bubble h2 {{ font-size: 1.25em; border-bottom: 1px solid var(--cp-border); padding-bottom: 3px; }}
        .bubble h3 {{ font-size: 1.1em; }}
        .bubble h4 {{ font-size: 1.0em; }}
        .bubble p  {{ margin: 6px 0; line-height: 1.6; }}
        .bubble ul, .bubble ol {{
            padding-left: 20px;
            margin: 6px 0;
            line-height: 1.7;
        }}
        .bubble ul li {{ list-style: disc; }}
        .bubble ol li {{ list-style: decimal; }}
        .bubble strong {{ font-weight: 700; }}
        .bubble em {{ font-style: italic; }}

        .tab-content {{
            display: none;
            flex: 1;
            flex-direction: column;
            overflow: hidden;
            height: 100%;
        }}
        .tab-content.active {{
            display: flex;
        }}

        .copilot-footer {{
            padding: 5px 11px;
            background: var(--cp-header-bg);
            border-top: 1px solid var(--cp-border);
            flex-shrink: 0;
        }}
        .input-wrapper {{
            background: var(--cp-bg);
            border: 1px solid var(--cp-border);
            border-radius: 8px;
            padding: 5px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            transition: border-color 0.2s;
            max-height: 200px;
        }}
        .input-wrapper:focus-within {{ border-color: var(--cp-accent); }}
        
        #chat-input {{
            border: none;
            background: transparent;
            color: var(--cp-text);
            resize: none;
            font-family: inherit;
            font-size: 14px;
            outline: none;
            height: 15px;
            max-height: 160px;
            padding: 2px;
            overflow-y: auto;
        }}
        .input-actions {{ display: flex; justify-content: flex-end; align-items: center; gap: 8px; }}

        .icon-btn {{
            width: 32px;
            height: 32px;
            border-radius: 6px;
            border: none;
            background: transparent;
            color: var(--cp-text);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s;
            font-size: 16px;
        }}
        .icon-btn:hover {{ background: var(--cp-border); }}
        
        .send-btn {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            border: none;
            background: var(--cp-accent);
            color: white;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.2s, background 0.2s;
        }}
        .send-btn:hover {{ background: var(--cp-accent-hover); transform: scale(1.05); }}
        .send-btn:active {{ transform: scale(0.95); }}

        /* Stop Button */
        .stop-btn {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            border: none;
            background: #e53935;
            color: white;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            transition: transform 0.2s, background 0.2s;
            margin-left: 6px;
        }}
        .stop-btn:hover {{ background: #c62828; transform: scale(1.05); }}
        .stop-btn:active {{ transform: scale(0.95); }}

        .footer-note {{ font-size: 10px; opacity: 0.5; text-align: center; margin-top: 8px; }}

        /* Floating Toggle Button */
        #injected-copilot-toggle-btn {{
            position: fixed;
            width: 50px;
            height: 50px;
            border-radius: 50%;
            border: none;
            background: #1e1e1e;
            color: white;
            font-size: 24px;
            cursor: grab;
            z-index: 10000000;
            box-shadow: 0 4px 12px var(--cp-shadow);
            transition: all 0.2s ease;
            align-items: center;
            justify-content: center;
        }}
        #injected-copilot-toggle-btn:hover {{ transform: scale(1.1); box-shadow: 0 6px 16px var(--cp-shadow); }}


        /* Typing Cursor Effect */
        .typing-cursor::after {{
            content: "●";
            display: inline-block;
            margin-left: 4px;
            color: var(--cp-accent);
            animation: cpBlink 0.8s infinite;
            font-size: 12px;
            vertical-align: middle;
        }}
        @keyframes cpBlink {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0; }} }}

        /* ---- Debug Tab Styles ---- */
        .debug-toolbar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 11px;
            background: var(--cp-header-bg);
            border-bottom: 1px solid var(--cp-border);
            flex-shrink: 0;
        }}
        .debug-label {{ font-size: 12px; font-weight: 600; opacity: 0.7; }}
        .debug-refresh-btn {{
            background: var(--cp-accent);
            border: none;
            color: white;
            padding: 4px 10px;
            border-radius: 5px;
            font-size: 12px;
            cursor: pointer;
            transition: background 0.2s;
        }}
        .debug-refresh-btn:hover {{ background: var(--cp-accent-hover); }}
        .debug-scroll-area {{
            flex: 1;
            overflow-y: auto;
            padding: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .debug-scroll-area::-webkit-scrollbar {{ width: 5px; }}
        .debug-scroll-area::-webkit-scrollbar-thumb {{ background: var(--cp-border); border-radius: 10px; }}
        .debug-placeholder {{ font-size: 12px; opacity: 0.5; text-align: center; margin-top: 40px; }}
        .dep-card {{
            background: var(--cp-bubble-bot);
            border: 1px solid var(--cp-bubble-bot-border);
            border-radius: 8px;
            padding: 8px 10px;
            font-size: 12px;
            line-height: 1.6;
        }}
        .dep-card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 4px;
        }}
        .dep-cell-num {{
            font-weight: 700;
            font-size: 13px;
            color: var(--cp-accent);
        }}
        .dep-badges {{ display: flex; gap: 4px; flex-wrap: wrap; }}
        .dep-badge {{
            background: rgba(71,161,255,0.15);
            border: 1px solid rgba(71,161,255,0.3);
            color: var(--cp-accent);
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 12px;
        }}
        .dep-badge.rev {{
            background: rgba(255,150,71,0.15);
            border-color: rgba(255,150,71,0.3);
            color: #ff9647;
        }}
        .dep-preview {{
            font-family: 'Fira Code', monospace;
            font-size: 11px;
            color: #abb2bf;
            white-space: pre-wrap;
            word-break: break-word;
            margin-top: 4px;
            opacity: 0.8;
        }}
        .dep-no-deps {{ font-size: 11px; opacity: 0.4; font-style: italic; }}

        /* ---- Header History Dropdown ---- */
        .history-dropdown {{
            position: absolute;
            top: 41px;
            left: 11px;
            right: 11px;
            max-height: min(360px, 55vh);
            background: var(--cp-bg);
            border: 1px solid var(--cp-border);
            border-radius: 10px;
            display: none;
            flex-direction: column;
            overflow: hidden;
            z-index: 25;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.35);
        }}
        .history-dropdown.active {{ display: flex; }}
        .history-dropdown-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 11px;
            background: var(--cp-header-bg);
            border-bottom: 1px solid var(--cp-border);
            flex-shrink: 0;
        }}
        .history-label {{ font-size: 12px; font-weight: 600; opacity: 0.8; }}
        .history-scroll-area {{
            flex: 1;
            overflow-y: auto;
            padding: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .history-scroll-area::-webkit-scrollbar {{ width: 5px; }}
        .history-scroll-area::-webkit-scrollbar-thumb {{ background: var(--cp-border); border-radius: 10px; }}
        .history-placeholder {{ font-size: 12px; opacity: 0.5; text-align: center; margin-top: 40px; }}
        .history-item {{
            background: var(--cp-bubble-bot);
            border: 1px solid var(--cp-bubble-bot-border);
            border-radius: 8px;
            padding: 8px 10px;
            cursor: pointer;
            text-align: left;
            color: var(--cp-text);
            transition: border-color 0.2s, background 0.2s;
        }}
        .history-item:hover {{ border-color: var(--cp-accent); }}
        .history-item.active {{
            border-color: var(--cp-accent);
            background: rgba(71,161,255,0.12);
        }}
        .history-title {{
            font-size: 12px;
            line-height: 1.5;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .history-meta {{
            margin-top: 2px;
            font-size: 10px;
            opacity: 0.65;
        }}
    `;
    document.head.appendChild(styleTag);

    // ---- Function to dynamically load Markdown-it if missing ----
    const ensureMarkdownIt = () => {{
        return new Promise((resolve) => {{
            if (window.markdownit) return resolve(true);
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/markdown-it@14.0.0/dist/markdown-it.min.js';
            script.onload = () => resolve(true);
            script.onerror = () => resolve(false);
            document.head.appendChild(script);
        }});
    }};

    // Panel Wrapper
    const wrapper = document.createElement('div');
    wrapper.id = 'injected-copilot-panel-wrapper';
    wrapper.innerHTML = content;

    // Toggle Button
    const btn = document.createElement('button');
    btn.id = 'injected-copilot-toggle-btn';
    btn.innerHTML = '🤖';

    // ---- Restore saved position or fall back to default ----
    const savedPos = JSON.parse(localStorage.getItem('cp_btn_pos') || 'null');
    if (savedPos) {{
        btn.style.left = savedPos.left;
        btn.style.top  = savedPos.top;
        btn.style.right = 'auto';
    }} else {{
        // Default: try to position near targetSelector, else bottom-right
        const targetElement = document.querySelector(targetSelector);
        if (targetElement) {{
            const rect = targetElement.getBoundingClientRect();
            btn.style.left  = (rect.left - 75) + 'px';
            btn.style.top   = (rect.top + (rect.height / 2) - 25) + 'px';
            btn.style.right = 'auto';
        }} else {{
            btn.style.right = '20px';
            btn.style.top   = '20px';
        }}
    }}

    // ---- Draggable logic (pointer events) ----
    let dragStartX, dragStartY, startLeft, startTop;
    let dragging = false;
    const DRAG_THRESHOLD = 5; // px — less than this = click, more = drag

    btn.addEventListener('pointerdown', (e) => {{
        if (e.button !== 0) return; // left-click / touch only
        e.preventDefault();
        btn.setPointerCapture(e.pointerId);
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        const cs = getComputedStyle(btn);
        startLeft = parseInt(cs.left, 10) || (window.innerWidth - 70);
        startTop  = parseInt(cs.top,  10) || 20;
        dragging  = false;
        btn.style.transition = 'none';
        btn.style.cursor = 'grabbing';
    }});

    btn.addEventListener('pointermove', (e) => {{
        if (!btn.hasPointerCapture(e.pointerId)) return;
        const dx = e.clientX - dragStartX;
        const dy = e.clientY - dragStartY;
        if (!dragging && Math.sqrt(dx*dx + dy*dy) > DRAG_THRESHOLD) {{
            dragging = true;
        }}
        if (dragging) {{
            let newLeft = startLeft + dx;
            let newTop  = startTop  + dy;
            // Clamp inside viewport
            newLeft = Math.max(0, Math.min(window.innerWidth  - 54, newLeft));
            newTop  = Math.max(0, Math.min(window.innerHeight - 54, newTop));
            btn.style.left  = newLeft + 'px';
            btn.style.top   = newTop  + 'px';
            btn.style.right = 'auto';
        }}
    }});

    btn.addEventListener('pointerup', (e) => {{
        btn.style.transition = 'box-shadow 0.2s ease, background 0.2s ease';
        btn.style.cursor = 'grab';
        if (dragging) {{
            // Persist position across reloads
            localStorage.setItem('cp_btn_pos', JSON.stringify({{
                left: btn.style.left,
                top:  btn.style.top
            }}));
        }} else {{
            // Short tap / click → toggle panel
            togglePanel();
        }}
        dragging = false;
    }});

    // ---- positionButton kept for resize handling ----
    function positionButton(btn, targetSelector) {{
        // Only reposition if user hasn't manually dragged the button
        if (localStorage.getItem('cp_btn_pos')) return;
        const targetElement = document.querySelector(targetSelector);
        if (targetElement) {{
            const rect = targetElement.getBoundingClientRect();
            btn.style.right = 'auto';
            btn.style.left  = (rect.left - 75) + 'px';
            btn.style.top   = (rect.top + (rect.height / 2) - 25) + 'px';
        }} else {{
            btn.style.right = '20px';
            btn.style.top   = '20px';
        }}
    }}

    // State Management
    let isOpen = false;
    const togglePanel = () => {{
        isOpen = !isOpen;
        wrapper.classList.toggle('active', isOpen);
        btn.innerHTML = isOpen ? '✕' : '🤖';
        btn.style.background = isOpen ? '#1e1e1e' : '#1e1e1e';
        if (isOpen) {{
            document.getElementById('chat-input').focus();
        }} else {{
            const dropdown = wrapper.querySelector('#history-dropdown');
            const historyBtn = wrapper.querySelector('#history-toggle-btn');
            if (dropdown) dropdown.classList.remove('active');
            if (historyBtn) historyBtn.classList.remove('active');
        }}
    }};

    document.body.appendChild(wrapper);
    document.body.appendChild(btn);

    // ---- Tab switching ----
    wrapper.querySelectorAll('.tab-item').forEach(btn => {{
        btn.addEventListener('click', () => {{
            const target = btn.dataset.tab;
            wrapper.querySelectorAll('.tab-item').forEach(b => b.classList.remove('active'));
            wrapper.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            wrapper.querySelector('#' + target).classList.add('active');

            if (target === 'debug-tab') renderGraph();
        }});
    }});

    // ---- Debug: Render dependency graph ----
    const debugContent = wrapper.querySelector('#debug-content');
    const historyToggleBtn = wrapper.querySelector('#history-toggle-btn');
    const historyDropdown = wrapper.querySelector('#history-dropdown');
    const historyList = wrapper.querySelector('#history-list');
    const historyNewBtn = wrapper.querySelector('#history-new');

    const getNotebookUrl = () => {{
        const rawHref = String(window.location.href || '').trim();
        if (!rawHref) return 'unknown_notebook';
        try {{
            const u = new URL(rawHref, window.location.origin);
            const parts = (u.pathname || '/').split('/').filter(Boolean);
            if (parts.length >= 3 && parts[0].toLowerCase() === 'code') {{
                return `${{u.origin.toLowerCase()}}/code/${{parts[1]}}/${{parts[2]}}`;
            }}
            const cleanPath = (u.pathname || '/').replace(/\/+$/, '') || '/';
            return `${{u.origin.toLowerCase()}}${{cleanPath}}`;
        }} catch (_e) {{
            return rawHref.split('#')[0].split('?')[0];
        }}
    }};

    const renderGraph = async () => {{
        debugContent.innerHTML = '<p class="debug-placeholder">⏳ Loading graph...</p>';
        try {{
            const res = await fetch('http://localhost:8080/graph?url=' + encodeURIComponent(getNotebookUrl()));
            const data = await res.json();
            if (data.error) {{
                debugContent.innerHTML = `<p class="debug-placeholder">⚠️ ${{data.error}}</p>`;
                return;
            }}
            if (!data.cells || data.cells.length === 0) {{
                debugContent.innerHTML = '<p class="debug-placeholder">No cells found yet. Cells are captured automatically while monitoring.</p>';
                return;
            }}
            debugContent.innerHTML = '';
            data.cells.forEach(cell => {{
                const card = document.createElement('div');
                card.className = 'dep-card';
                const depBadges = (cell.dependencies || []).map(d => `<span class="dep-badge">← Cell ${{d}}</span>`).join('');
                const revBadges = (cell.reverse_dependencies || []).map(r => `<span class="dep-badge rev">→ Cell ${{r}}</span>`).join('');
                const noDeps = !depBadges && !revBadges;
                const preview = cell.input_preview ? cell.input_preview.replace(/</g,'&lt;').replace(/>/g,'&gt;') : '';
                card.innerHTML = `
                    <div class="dep-card-header">
                        <span class="dep-cell-num">Cell ${{cell.cell_number}}</span>
                        <div class="dep-badges">${{depBadges}}${{revBadges}}</div>
                    </div>
                    ${{noDeps ? '<span class="dep-no-deps">No dependencies</span>' : ''}}
                    ${{preview ? `<div class="dep-preview">${{preview}}</div>` : ''}}
                `;
                debugContent.appendChild(card);
            }});
        }} catch(e) {{
            debugContent.innerHTML = `<p class="debug-placeholder">⚠️ Could not reach backend: ${{e.message}}</p>`;
        }}
    }};

    wrapper.querySelector('#debug-refresh').addEventListener('click', renderGraph);

    const textarea = wrapper.querySelector('#chat-input');
    textarea.oninput = function() {{
        this.style.height = 'auto';
        const newHeight = Math.min(this.scrollHeight, 160);
        this.style.height = newHeight + 'px';
        this.style.overflowY = this.scrollHeight > 160 ? 'auto' : 'hidden';
    }};

    // ============================================================
    // MARKDOWN-IT BASED FORMATTER: format_llm_response
    // ============================================================
    // Enhance code blocks rendered by markdown-it with Copy buttons
    const enhanceCodeBlocks = (container) => {{
        container.querySelectorAll('pre code').forEach((codeEl) => {{
            const pre = codeEl.parentNode;
            if (pre.parentNode.classList.contains('code-block-wrapper')) return; // already enhanced

            const wrapper = document.createElement('div');
            wrapper.className = 'code-block-wrapper';
            wrapper.style.margin = '15px 0';

            // Detect language from class e.g. "language-python"
            const langClass = codeEl.className.match(/language-(\w+)/);
            const lang = langClass ? langClass[1] : 'code';

            const header = document.createElement('div');
            header.className = 'code-header';
            header.innerHTML = `<span>${{lang}}</span>
                <button class="copy-btn">
                    <svg class="copy-icon" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                    </svg>
                    <span class="btn-text">Copy</span>
                </button>`;

            const copyBtn = header.querySelector('.copy-btn');
            const btnText = header.querySelector('.btn-text');
            const icon = header.querySelector('svg');

            copyBtn.addEventListener('click', async () => {{
                try {{
                    await navigator.clipboard.writeText(codeEl.innerText);
                    copyBtn.classList.add('copied');
                    btnText.innerText = 'Copied!';
                    const orig = icon.innerHTML;
                    icon.innerHTML = '<polyline points="20 6 9 17 4 12"></polyline>';
                    setTimeout(() => {{
                        copyBtn.classList.remove('copied');
                        btnText.innerText = 'Copy';
                        icon.innerHTML = orig;
                    }}, 2000);
                }} catch(e) {{ console.error('Copy failed', e); }}
            }});

            pre.parentNode.insertBefore(wrapper, pre);
            pre.className = 'code-block';
            pre.style.cssText = 'padding:14px; margin:0; overflow-x:auto;';
            wrapper.appendChild(header);
            wrapper.appendChild(pre);
        }});
    }};

    // Main formatter: runs markdown-it on full accumulated text, then enhances code blocks
    const format_llm_response = (text) => {{
        if (!text) return "";
        // Normalize line endings
        const normalized = text.replace(/\r\n/g, '\n');

        // Render markdown to HTML via markdown-it (safe check)
        let html = normalized;
        if (window.markdownit) {{
            try {{
                html = window.markdownit({{ html: false, linkify: true, typographer: true }}).render(normalized);
            }} catch(e) {{ console.error("MD render error", e); }}
        }}

        // Parse into a temp DOM element so we can enhance code blocks
        const temp = document.createElement('div');
        temp.innerHTML = html;
        enhanceCodeBlocks(temp);

        return temp.innerHTML;
    }};

    const chatHistory = wrapper.querySelector('#chat-history');
    const appendMessage = async (text, role) => {{
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${{role}}`;
        msgDiv.innerHTML = `<div class="bubble">${{text}}</div>`;
        chatHistory.appendChild(msgDiv);
        
        // Render it with formatting
        const bubble = msgDiv.querySelector('.bubble');
        await ensureMarkdownIt();
        bubble.innerHTML = format_llm_response(text);
        
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }};

    const renderDefaultGreeting = async () => {{
        chatHistory.innerHTML = '';
        await appendMessage('Hello! I am your AI assistant. How can I help you today?', 'assistant');
    }};

    const renderChatMessages = async (messages) => {{
        const list = Array.isArray(messages) ? messages : [];
        if (!list.length) {{
            await renderDefaultGreeting();
            return;
        }}

        chatHistory.innerHTML = '';
        for (const msg of list) {{
            const role = (msg && msg.role) === 'user' ? 'user' : 'assistant';
            const text = String((msg && msg.content) || '').trim();
            if (text) await appendMessage(text, role);
        }}
    }};

    let shouldAutoScroll = true;
    chatHistory.onscroll = () => {{
        const threshold = 60; // Slightly larger for better 're-catch'
        const distanceToBottom = chatHistory.scrollHeight - chatHistory.clientHeight - chatHistory.scrollTop;
        shouldAutoScroll = distanceToBottom < threshold;
    }};

    const scrollToBottom = (instant = false) => {{
        if (shouldAutoScroll) {{
            chatHistory.scrollTo({{
                top: chatHistory.scrollHeight,
                behavior: instant ? 'auto' : 'smooth'
            }});
        }}
    }};

    // ---- Stop / AbortController state ----
    let currentAbortController = null;

    const sendBtn  = wrapper.querySelector('#chat-send');
    const stopBtn  = wrapper.querySelector('#chat-stop');

    const tabStorageKey = 'cp_tab_id';
    const tabId = (() => {{
        let existing = sessionStorage.getItem(tabStorageKey);
        if (existing) return existing;
        const generated = (window.crypto && typeof window.crypto.randomUUID === 'function')
            ? window.crypto.randomUUID()
            : ('tab_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2));
        sessionStorage.setItem(tabStorageKey, generated);
        return generated;
    }})();

    const hashString = (raw) => {{
        let hash = 0;
        for (let i = 0; i < raw.length; i++) {{
            hash = ((hash << 5) - hash) + raw.charCodeAt(i);
            hash |= 0;
        }}
        return Math.abs(hash);
    }};

    const getNotebookSessionKey = () => {{
        const notebookUrl = getNotebookUrl() || 'unknown_notebook';
        return 'cp_session_id_' + hashString(notebookUrl) + '_' + tabId;
    }};

    const getCurrentMode = () => {{
        const activeTab = wrapper.querySelector('.tab-item.active');
        if (activeTab && activeTab.dataset && activeTab.dataset.tab === 'debug-tab') {{
            return 'dependency';
        }}
        return 'simple';
    }};

    const setStreaming = (active) => {{
        sendBtn.disabled = active;
        sendBtn.style.opacity = active ? '0.5' : '1';
        stopBtn.style.display  = active ? 'flex' : 'none';
    }};

    const closeHistoryDropdown = () => {{
        if (historyDropdown) historyDropdown.classList.remove('active');
        if (historyToggleBtn) historyToggleBtn.classList.remove('active');
    }};

    const toggleHistoryDropdown = async (event) => {{
        if (event) event.stopPropagation();
        if (!historyDropdown) return;

        const shouldOpen = !historyDropdown.classList.contains('active');
        closeHistoryDropdown();

        if (shouldOpen) {{
            await loadConversationList();
            historyDropdown.classList.add('active');
            if (historyToggleBtn) historyToggleBtn.classList.add('active');
        }}
    }};

    if (historyToggleBtn) {{
        historyToggleBtn.addEventListener('click', (event) => {{
            toggleHistoryDropdown(event).catch((err) => {{
                console.warn('Could not toggle history dropdown:', err);
            }});
        }});
    }}

    if (historyDropdown) {{
        historyDropdown.addEventListener('click', (event) => event.stopPropagation());
    }}

    document.addEventListener('click', (event) => {{
        if (!historyDropdown || !historyDropdown.classList.contains('active')) return;
        const target = event.target;
        if (historyDropdown.contains(target)) return;
        if (historyToggleBtn && historyToggleBtn.contains(target)) return;
        closeHistoryDropdown();
    }});

    const formatHistoryTime = (value) => {{
        if (!value) return 'No messages yet';
        try {{
            const d = new Date(String(value).replace(' ', 'T'));
            if (Number.isNaN(d.getTime())) return String(value);
            return d.toLocaleString();
        }} catch (_e) {{
            return String(value);
        }}
    }};

    const renderConversationList = (conversations, activeSessionId) => {{
        const list = Array.isArray(conversations) ? conversations : [];
        historyList.innerHTML = '';

        if (!list.length) {{
            historyList.innerHTML = '<p class="history-placeholder">No saved conversations yet.</p>';
            return;
        }}

        list.forEach((conv) => {{
            const sid = String((conv && conv.session_id) || '').trim();
            if (!sid) return;

            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'history-item' + (sid === activeSessionId ? ' active' : '');
            item.dataset.sessionId = sid;

            const title = document.createElement('div');
            title.className = 'history-title';
            title.textContent = String((conv && conv.title) || 'New conversation');

            const meta = document.createElement('div');
            meta.className = 'history-meta';
            const count = Number((conv && conv.message_count) || 0);
            meta.textContent = `${{count}} messages • ${{formatHistoryTime(conv && conv.updated_at)}}`;

            item.appendChild(title);
            item.appendChild(meta);
            item.addEventListener('click', () => switchConversation(sid));
            historyList.appendChild(item);
        }});
    }};

    const loadConversationList = async () => {{
        try {{
            const notebookUrl = getNotebookUrl();
            const notebookSessionKey = getNotebookSessionKey();
            const params = new URLSearchParams({{
                url: notebookUrl,
                tab_id: tabId
            }});
            const response = await fetch('http://localhost:8080/history/list?' + params.toString());
            if (!response.ok) return;

            const payload = await response.json();
            const stored = sessionStorage.getItem(notebookSessionKey);
            const active = String(stored || payload.active_session_id || '').trim() || null;
            if (!stored && active) sessionStorage.setItem(notebookSessionKey, active);

            renderConversationList(payload.conversations, active);
        }} catch (err) {{
            console.warn('Could not load conversation list:', err);
        }}
    }};

    const switchConversation = async (targetSessionId) => {{
        if (!targetSessionId) return;

        try {{
            const notebookUrl = getNotebookUrl();
            const notebookSessionKey = getNotebookSessionKey();
            const params = new URLSearchParams({{
                url: notebookUrl,
                tab_id: tabId,
                session_id: targetSessionId
            }});
            const response = await fetch('http://localhost:8080/history?' + params.toString());
            if (!response.ok) return;

            const payload = await response.json();
            const sid = String((payload && payload.session_id) || '').trim();
            if (sid) sessionStorage.setItem(notebookSessionKey, sid);

            const messages = Array.isArray(payload && payload.messages) ? payload.messages : [];
            await renderChatMessages(messages);
            await loadConversationList();

            const chatTabBtn = wrapper.querySelector('.tab-item[data-tab="chat-tab"]');
            if (chatTabBtn) chatTabBtn.click();
            closeHistoryDropdown();
        }} catch (err) {{
            console.warn('Could not switch conversation:', err);
        }}
    }};

    const startNewConversation = async () => {{
        if (currentAbortController) currentAbortController.abort();

        try {{
            const notebookUrl = getNotebookUrl();
            const notebookSessionKey = getNotebookSessionKey();
            const params = new URLSearchParams({{
                url: notebookUrl,
                tab_id: tabId
            }});
            const response = await fetch('http://localhost:8080/history/new?' + params.toString(), {{ method: 'POST' }});
            if (!response.ok) return;

            const payload = await response.json();
            const sid = String((payload && payload.session_id) || '').trim();
            if (sid) sessionStorage.setItem(notebookSessionKey, sid);

            await renderDefaultGreeting();
            await loadConversationList();

            const chatTabBtn = wrapper.querySelector('.tab-item[data-tab="chat-tab"]');
            if (chatTabBtn) chatTabBtn.click();
            closeHistoryDropdown();
        }} catch (err) {{
            console.warn('Could not start a new conversation:', err);
        }}
    }};

    if (historyNewBtn) historyNewBtn.addEventListener('click', startNewConversation);

    const loadPersistedHistory = async () => {{
        try {{
            const notebookUrl = getNotebookUrl();
            const notebookSessionKey = getNotebookSessionKey();
            const sid = sessionStorage.getItem(notebookSessionKey) || null;
            const params = new URLSearchParams({{
                url: notebookUrl,
                tab_id: tabId
            }});
            if (sid) params.set('session_id', sid);

            const response = await fetch('http://localhost:8080/history?' + params.toString());
            if (!response.ok) return;

            const payload = await response.json();
            const session = String((payload && payload.session_id) || '').trim();
            if (session) sessionStorage.setItem(notebookSessionKey, session);

            const messages = Array.isArray(payload && payload.messages) ? payload.messages : [];
            await renderChatMessages(messages);
        }} catch (err) {{
            console.warn('Could not load local chat history:', err);
            await renderDefaultGreeting();
        }}
    }};

    const historyLoadPromise = (async () => {{
        await loadPersistedHistory();
        await loadConversationList();
    }})();

    const sendPrompt = async (prompt, forcedMode = null) => {{
        await historyLoadPromise;
        if (!prompt || currentAbortController) return;

        const selectedMode = forcedMode || getCurrentMode();

        appendMessage(prompt, 'user');

        shouldAutoScroll = true;
        chatHistory.scrollTo({{ top: chatHistory.scrollHeight, behavior: 'smooth' }});

        const botMsgDiv = document.createElement('div');
        botMsgDiv.className = 'message assistant';
        botMsgDiv.innerHTML = '<div class="bubble typing-cursor">Thinking...</div>';
        chatHistory.appendChild(botMsgDiv);

        const bubble = botMsgDiv.querySelector('.bubble');
        let fullText = "";

        // Create AbortController for this request
        currentAbortController = new AbortController();
        const {{ signal }} = currentAbortController;

        setStreaming(true);

        const notebookSessionKey = getNotebookSessionKey();
        const notebookUrl = getNotebookUrl();
        const body = {{
            prompt,
            mode: selectedMode,
            debug: selectedMode === 'dependency',
            session_id: sessionStorage.getItem(notebookSessionKey) || null,
            tab_id: tabId,
            notebook_url: notebookUrl
        }};

        try {{
            await ensureMarkdownIt(); // Ensure markdown-it is loaded before processing LLM response
            const response = await fetch('http://localhost:8080/chat', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(body),
                signal
            }});

            const sid = response.headers.get('X-Session-ID');
            if (sid) sessionStorage.setItem(notebookSessionKey, sid);

            if (!response.body) throw new Error("No response body");

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            bubble.classList.add('typing-cursor');

            while (true) {{
                const {{ done, value }} = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, {{ stream: true }});
                if (fullText === "") bubble.innerHTML = "";
                fullText += chunk;
                bubble.innerHTML = format_llm_response(fullText);
                scrollToBottom(true);
            }}
            bubble.classList.remove('typing-cursor');

        }} catch (err) {{
            if (err.name === 'AbortError') {{
                // User stopped — clean up silently, keep whatever was rendered
                bubble.classList.remove('typing-cursor');
                if (fullText === "") bubble.innerHTML = '<em style="color:#aaa;">Stopped.</em>';
            }} else {{
                bubble.innerHTML = '<span style="color:red">Error: ' + err.message + '</span>';
                bubble.classList.remove('typing-cursor');
            }}
        }} finally {{
            currentAbortController = null;
            setStreaming(false);
            loadConversationList().catch(() => {{}});
        }}
    }};

    const sendMessage = async () => {{
        const prompt = textarea.value.trim();
        if (!prompt) return;

        textarea.value = '';
        textarea.style.height = 'auto';
        await sendPrompt(prompt);
    }};

    // Stop button handler
    stopBtn.onclick = () => {{
        if (currentAbortController) {{
            currentAbortController.abort();
        }}
        // Also notify the backend to stop generating
        const notebookSessionKey = getNotebookSessionKey();
        const sid = sessionStorage.getItem(notebookSessionKey);
        if (sid) {{
            fetch('http://localhost:8080/stop?session_id=' + encodeURIComponent(sid), {{
                method: 'POST'
            }}).catch(() => {{}});
        }}
    }};

    window.addEventListener('message', (event) => {{
        const payload = event.data;
        if (!payload || payload.type !== 'cp_explain_error') return;

        const rawError = String(payload.errorText || '').trim();
        if (!rawError) return;

        const parsedCell = parseInt(payload.cellNumber, 10);
        const cellLabel = Number.isNaN(parsedCell) ? 'a notebook cell' : `cell ${{parsedCell}}`;
        const explainPrompt = `Explain this ${{cellLabel}} error, why it happened, and how to fix it.\n\n${{rawError}}`;

        if (!isOpen) togglePanel();
        sendPrompt(explainPrompt, 'explain_error');
    }});

    sendBtn.onclick  = sendMessage;
    textarea.onkeydown = (e) => {{ if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); sendMessage(); }} }};

    // Header Actions
    // Removed #header-toggle-btn listener as requested

    // Responsive Handling
    const handleResize = () => {{
        positionButton(btn, targetSelector);
        if (window.innerWidth < 600) {{
            wrapper.style.width = '100vw';
        }} else {{
            wrapper.style.width = '395px';
        }}
    }};
    window.addEventListener('resize', handleResize);
    handleResize();


    return 'SUCCESS';
}})({content_quoted}, {selector_quoted})
"""

# ========== CRAFTING SERVER ==========
class CraftingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/history/list'):
            # ---- Conversation list endpoint (tab-scoped) ----
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            notebook_url = _normalize_notebook_url(params.get('url', [''])[0])
            tab_id = params.get('tab_id', [''])[0] or None
            limit = params.get('limit', ['30'])[0]

            try:
                limit_i = int(limit)
            except Exception:
                limit_i = 30

            payload = memory_store.list_conversations(notebook_url, tab_id=tab_id, limit=limit_i)
            body = json.dumps(payload).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith('/history'):
            # ---- Local chat history endpoint (tab-scoped) ----
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            notebook_url = _normalize_notebook_url(params.get('url', [''])[0])
            tab_id = params.get('tab_id', [''])[0] or None
            requested_session = params.get('session_id', [''])[0] or None

            session_id = memory_store.resolve_session(notebook_url, requested_session, tab_id=tab_id)
            history = memory_store.get_history(
                notebook_url,
                session_id,
                limit=MAX_HISTORY_MESSAGES * 2,
                tab_id=tab_id
            )

            body = json.dumps({'session_id': session_id, 'messages': history}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith('/graph'):
            # ---- Dependency graph data endpoint ----
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            notebook_url = _normalize_notebook_url(params.get('url', [''])[0])
            graph_data = profile_manager.get_graph_data(notebook_url)
            body = json.dumps(graph_data).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()
            self.wfile.write(body)
            return

        # ---- Existing sandbox GET handler ----
        # The Local Design Sandbox
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        content_quoted = json.dumps(DEFAULT_PANEL_CONTENT)
        selector_quoted = json.dumps(DEFAULT_TARGET_SELECTOR)
        
        preview_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>GUI Craft Sandbox</title>
            <script src="https://cdn.jsdelivr.net/npm/markdown-it@14.0.0/dist/markdown-it.min.js"></script>
            <style>
                body { background-color: #f0f2f5; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; height: 150vh; padding: 40px; }
                .sandbox-header { border-bottom: 2px solid #ddd; margin-bottom: 30px; }
                .mock-site { background: white; border: 1px solid #ddd; padding: 40px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
                .target-zone { border: 2px dashed #0078d4; padding: 20px; display: inline-block; margin-top: 20px; border-radius: 4px; }
                code { background: #eee; padding: 2px 5px; border-radius: 3px; }
            </style>
        </head>
        <body>
            <div class="sandbox-header">
                <h1>🎨 GUI Craft Workspace</h1>
                <p>Modify <code>gui-craft.py</code> and refresh this page to see your changes.</p>
            </div>
            
            <div class="mock-site">
                <h2>Mock Target Website</h2>
                <p>This area simulates the Kaggle interface. The blue dashed box represents the target container.</p>
                
                <div class="target-zone">
                    <p>Container: <code>sc-biDvOf gfGqWC</code></p>
                    <div id="site-content">
                        <div class="sc-jCbqkY fAtosG">
                            <div class="sc-khOpgq kyUkBa">
                                <div class="sc-biDvOf gfGqWC">
                                    <button style="background: #20beff; color: white; border: none; padding: 10px 20px; border-radius: 4px; font-weight: bold;">
                                        Target Button
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                """ + JS_INJECTION_TEMPLATE.format(
                    content_quoted=content_quoted,
                    selector_quoted=selector_quoted
                ) + """
            </script>
        </body>
        </html>
        """
        self.wfile.write(preview_html.encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()

    def do_POST(self):
        if self.path.startswith('/history/new'):
            # ---- Create/switch to a fresh conversation for this tab ----
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            notebook_url = _normalize_notebook_url(params.get('url', [''])[0])
            tab_id = params.get('tab_id', [''])[0] or None

            fresh_session_id = str(uuid.uuid4())
            session_id = memory_store.resolve_session(notebook_url, fresh_session_id, tab_id=tab_id)

            body = json.dumps({'session_id': session_id, 'messages': []}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith('/stop'):
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            session_id = params.get('session_id', [''])[0]

            if session_id:
                try:
                    requests.post(
                        NGROK_STOP_URL,
                        params={'session_id': session_id},
                        timeout=10
                    )
                except Exception as exc:
                    print(f"[STOP] Failed forwarding stop signal: {exc}")

            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()
            return

        if self.path == '/chat':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                prompt = str(
                    data.get("prompt")
                    or data.get("user_prompt")
                    or data.get("query")
                    or data.get("input")
                    or ""
                ).strip()
                if not prompt:
                    self.send_response(400)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Access-Control-Allow-Private-Network', 'true')
                    self.end_headers()
                    self.wfile.write(b"Missing prompt")
                    return

                notebook_url = _normalize_notebook_url(str(data.get("notebook_url", "")).strip())
                is_debug = bool(data.get("debug", False))
                requested_mode = str(data.get("mode", "")).strip()
                requested_session = str(data.get("session_id") or "").strip() or None
                requested_tab_id = str(data.get("tab_id") or "").strip() or None

                mode = _resolve_mode(prompt, requested_mode, is_debug)
                session_id = memory_store.resolve_session(notebook_url, requested_session, tab_id=requested_tab_id)

                # Pattern: "cell X" / "cell: X" / "cell X:"
                cell_match = re.search(r'cell[:\s]+(\d+)', prompt, re.IGNORECASE)
                nb_builder = dep_manager.get_builder(notebook_url) if notebook_url else None
                context_text = None

                # Build dependency context only when needed by mode or prompt pattern.
                if (mode == 'dependency' or is_debug or cell_match) and nb_builder:
                    target_cell_id = int(cell_match.group(1)) if cell_match else 0

                    print(f"[DEBUG] Building context for cell {target_cell_id} (notebook: {notebook_url[:60]}...)")
                    context_package = nb_builder.build_context_for_cell(target_cell_id)

                    if 'error' not in context_package:
                        context_text = context_package.get('context_text', '')
                        print(f"[DEBUG] Context Prepended (Graph size: {len(context_package.get('dependencies', []))} nodes)")

                history = memory_store.get_history(
                    notebook_url,
                    session_id,
                    limit=MAX_HISTORY_MESSAGES,
                    tab_id=requested_tab_id
                )
                memory_store.append_message(notebook_url, session_id, 'user', prompt, mode, tab_id=requested_tab_id)

                system_prompt = _get_system_prompt(mode, prompt=prompt, has_context=bool(context_text))
                normalized_history = []
                for msg in history:
                    if not isinstance(msg, dict):
                        continue
                    role = str(msg.get('role') or '').strip().lower()
                    content = str(msg.get('content') or '').strip()
                    if role in {'user', 'assistant', 'system'} and content:
                        normalized_history.append({'role': role, 'content': content})

                messages = []
                if system_prompt:
                    messages.append({'role': 'system', 'content': system_prompt})
                if context_text:
                    messages.append({'role': 'system', 'content': f'Notebook context:\n{context_text}'})
                messages.extend(normalized_history)
                messages.append({'role': 'user', 'content': prompt})

                payload = {
                    "prompt": prompt,
                    "user_prompt": prompt,
                    "query": prompt,
                    "input": prompt,
                    "history": normalized_history,
                    "chat_history": normalized_history,
                    "messages": messages,
                    "conversation": messages,
                    "system_prompt": system_prompt,
                    "scenario_prompt": system_prompt,
                    "local_system_prompt": system_prompt,
                    "mode": mode,
                    "session_id": session_id,
                    "notebook_url": notebook_url,
                    "tab_id": requested_tab_id,
                }
                if context_text:
                    payload["context"] = context_text

                print(
                    f"[CHAT] Forwarding sid={session_id} mode={mode}, "
                    f"history={len(normalized_history)} messages={len(messages)}"
                )

                try:
                    response = requests.post(NGROK_URL, json=payload, stream=True, timeout=(10, 300))
                except Exception as e:
                    print(f"[ERROR] NGROK Connection Failed: {e}")
                    self.send_response(502)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Access-Control-Allow-Private-Network', 'true')
                    self.end_headers()
                    self.wfile.write(f"Proxy Error: Could not reach backend AI server. ({e})".encode('utf-8'))
                    return

                backend_sid = response.headers.get('X-Session-ID') or session_id
                if backend_sid != session_id:
                    memory_store.rebind_session(notebook_url, session_id, backend_sid, tab_id=requested_tab_id)
                    session_id = backend_sid

                # Send matching status and stream headers
                self.send_response(response.status_code)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('X-Accel-Buffering', 'no')  # Disable buffering on proxies
                self.send_header('X-Session-ID', session_id)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
                self.end_headers()

                if response.status_code == 200:
                    print("[CHAT] Streaming response started...")
                    assistant_bytes = bytearray()
                    for chunk in response.iter_content(chunk_size=1):
                        if chunk:
                            assistant_bytes.extend(chunk)
                            self.wfile.write(chunk)
                            self.wfile.flush()

                    assistant_text = assistant_bytes.decode('utf-8', errors='ignore').strip()
                    if assistant_text:
                        memory_store.append_message(notebook_url, session_id, 'assistant', assistant_text, mode, tab_id=requested_tab_id)
                        memory_store.trim_history(notebook_url, session_id, keep_last=MAX_HISTORY_MESSAGES * 2, tab_id=requested_tab_id)

                    print("[CHAT] Streaming complete.")
                else:
                    print(f"[ERROR] NGROK returned {response.status_code}")
                    self.wfile.write(f"AI Server Error: {response.status_code}".encode('utf-8'))
            except Exception as e:
                print(f"[CRITICAL] local Server Error: {e}")
                if not self.wfile.closed:
                    self.send_response(500)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Access-Control-Allow-Private-Network', 'true')
                    self.end_headers()
                    self.wfile.write(str(e).encode('utf-8'))

# ========== CDP MONITORING (REUSED) ==========
import hashlib as _hashlib

def get_all_tabs(port):
    try: return requests.get(f'http://localhost:{port}/json').json()
    except: return []

def filter_notebook_tabs(tabs):
    return {t['id']: t for t in tabs if '/edit' in t.get('url', '') and t.get('webSocketDebuggerUrl')}

def cdp_evaluate(ws_url, expression):
    ws = None
    try:
        ws = websocket.create_connection(ws_url, timeout=5, suppress_origin=True)
        msg = {"id": 1, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True}}
        ws.send(json.dumps(msg))
        return json.loads(ws.recv())
    except Exception as e: return {"error": str(e)}
    finally:
        if ws: ws.close()


# ========== CELL EXTRACTION (from iframe via async JS) ==========
_EXTRACTION_JS = """
(function() {
    return new Promise((resolve) => {
        const MAX_RETRIES = 5;
        const DELAY = 300;
        let attempts = 0;
        function tryExtract() {
            const cells = document.querySelectorAll('.lm-Widget.jp-Cell.jp-CodeCell');
            if (cells.length === 0 && attempts < MAX_RETRIES) { attempts++; setTimeout(tryExtract, DELAY); return; }
            const result = [];
            cells.forEach((cell, index) => {
                let cellId = cell.id || cell.getAttribute('data-uuid') || `cell-pos-${index}`;
                let input = '';
                const cm6 = cell.querySelectorAll('.cm-editor .cm-line');
                if (cm6.length > 0) {
                    input = Array.from(cm6).map(l => l.innerText).join('\\n');
                } else {
                    const cm5 = cell.querySelectorAll('.CodeMirror-line');
                    if (cm5.length > 0) {
                        input = Array.from(cm5).map(l => l.innerText).join('\\n');
                    } else {
                        const ta = cell.querySelector('.jp-InputArea-editor textarea');
                        if (ta) input = ta.value;
                    }
                }
                const outNode = cell.querySelector('.jp-OutputArea-output') || cell.querySelector('.jp-OutputArea');
                result.push({ cell_id: cellId, cell_number: index + 1, input: input.trim(), output: outNode ? outNode.innerText.trim() : '' });
            });
            resolve(result);
        }
        tryExtract();
    });
})()
"""


def _extract_cells_from_iframe(iframe_ws_url):
    """Connect to iframe CDP target and extract all cell inputs/outputs."""
    ws = None
    try:
        ws = websocket.create_connection(iframe_ws_url, timeout=10, suppress_origin=True)
        msg = {"id": 1, "method": "Runtime.evaluate",
               "params": {"expression": _EXTRACTION_JS, "returnByValue": True, "awaitPromise": True}}
        ws.send(json.dumps(msg))
        resp = json.loads(ws.recv())
        if 'result' in resp and 'result' in resp['result']:
            return resp['result']['result'].get('value')
    except Exception as exc:
        print(f"   [SCRAPER] Extraction error: {exc}")
    finally:
        if ws: ws.close()
    return None


def _cell_hash(cell):
    s = f"{cell['input']}\x00{cell['output']}".encode('utf-8')
    return _hashlib.md5(s).hexdigest()


def _save_cells_json(cells, full_url):
    filename = _sanitize_url_to_filename(full_url)
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(cells, f, indent=4, ensure_ascii=False)
    except Exception as exc:
        print(f"   [SCRAPER] JSON save failed: {exc}")


# ========== NOTEBOOK PROFILE MANAGER ==========
class NotebookProfileManager:
    """
    Tracks one profile per notebook URL:
      - extracts cells every polling cycle
      - detects changes via MD5 hashing
      - persists JSON to disk (same filename as scrapper.py)
      - signals dep_manager to refresh graph on next HTTP request
    """
    def __init__(self):
        self._profiles: Dict[str, dict] = {}

    def _get_iframe_ws(self, port, page_target_id):
        """Return the WebSocket URL of the editor iframe for this page."""
        all_targets = get_all_tabs(port)
        iframes = [t for t in all_targets
                   if t.get('type') == 'iframe' and t.get('parentId') == page_target_id]
        if not iframes:
            iframes = [t for t in all_targets
                       if t.get('type') == 'iframe' and 'kaggle' in t.get('url', '').lower()]
        if not iframes:
            return None
        return iframes[0].get('webSocketDebuggerUrl')

    def scrape_notebook(self, port, page_target_id, full_url):
        """
        Extract cells from the iframe, detect changes, save JSON, invalidate graph.
        Called every monitoring cycle for each active notebook tab.
        """
        iframe_ws = self._get_iframe_ws(port, page_target_id)
        if not iframe_ws:
            return

        cells = _extract_cells_from_iframe(iframe_ws)
        if not cells:
            return

        new_hashes = {c['cell_number']: _cell_hash(c) for c in cells}
        profile = self._profiles.get(full_url, {})
        old_hashes = profile.get('hashes', {})

        changed = (old_hashes != new_hashes)
        if changed or full_url not in self._profiles:
            _save_cells_json(cells, full_url)
            self._profiles[full_url] = {
                'cells': cells,
                'hashes': new_hashes,
            }
            if changed:
                print(f"   [SCRAPER] Updated JSON for: {full_url.split('/')[-1]}")
            else:
                print(f"   [SCRAPER] Initial snapshot for: {full_url.split('/')[-1]}")

    def get_cells(self, full_url):
        """Return the last known cells for a notebook URL."""
        return self._profiles.get(full_url, {}).get('cells', [])

    def get_graph_data(self, full_url):
        """
        Return serialisable dependency graph data for the Debug panel.
        Triggers dep_manager to load/refresh the graph as needed.
        """
        nb_builder = dep_manager.get_builder(full_url)
        cells = self.get_cells(full_url)
        if not cells or not nb_builder:
            return {'cells': [], 'error': 'Graph not ready. Ensure this notebook is being scraped.'}

        result = []
        tracker = nb_builder.tracker
        for cell in cells:
            num = cell['cell_number']
            deps = tracker.get_dependencies(num, transitive=False)
            rev  = tracker.get_reverse_dependencies(num)
            result.append({
                'cell_number': num,
                'input_preview': cell['input'][:120],
                'dependencies': deps,
                'reverse_dependencies': rev,
            })
        return {'cells': result}


# Global profile manager
profile_manager = NotebookProfileManager()


def inject_panel(ws_url):
    content_quoted = json.dumps(DEFAULT_PANEL_CONTENT)
    selector_quoted = json.dumps(DEFAULT_TARGET_SELECTOR)
    js_code = JS_INJECTION_TEMPLATE.format(content_quoted=content_quoted, selector_quoted=selector_quoted)
    return cdp_evaluate(ws_url, js_code)

# ========== CELL INDEX BADGE INJECTION (CDP Iframe Target Approach) ==========
CELL_BADGE_JS = """
(function() {
    const CELL_SELECTOR = '.lm-Widget.jp-Cell.jp-CodeCell';
    const ERROR_REGEX = /(traceback|exception|\berror\b|\bnameerror\b|\btypeerror\b|\bvalueerror\b|\bsyntaxerror\b|\bkeyerror\b|\bindexerror\b|\battributeerror\b|\bimporterror\b|\bmodulenotfounderror\b|\bzerodivisionerror\b)/i;
    const ERROR_TYPE_REGEX = /\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b/;

    if (window.__injCellBadgeObserver && typeof window.__injCellBadgeObserver.disconnect === 'function') {
        window.__injCellBadgeObserver.disconnect();
    }

    // Cleanup stale artifacts from older script runs.
    document.querySelectorAll('.inj-badge').forEach((b) => b.remove());
    document.querySelectorAll('.inj-explain-error-inline').forEach((e) => e.remove());
    document.querySelectorAll('.inj-error-controls').forEach((c) => c.remove());

    const createExplainButton = () => {
        const explainBtn = document.createElement('button');
        explainBtn.className = 'inj-explain-error-btn';
        explainBtn.innerText = 'Explain Error';
        explainBtn.style.background = '#b71c1c';
        explainBtn.style.color = '#ffffff';
        explainBtn.style.border = 'none';
        explainBtn.style.borderRadius = '999px';
        explainBtn.style.fontSize = '11px';
        explainBtn.style.fontWeight = '600';
        explainBtn.style.padding = '3px 10px';
        explainBtn.style.lineHeight = '1.2';
        explainBtn.style.cursor = 'pointer';

        explainBtn.addEventListener('mouseenter', () => {
            explainBtn.style.background = '#8e0000';
        });
        explainBtn.addEventListener('mouseleave', () => {
            explainBtn.style.background = '#b71c1c';
        });

        explainBtn.addEventListener('click', (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            window.top.postMessage({
                type: 'cp_explain_error',
                cellNumber: parseInt(explainBtn.dataset.cellNumber || '0', 10) || undefined,
                errorText: String(explainBtn.dataset.errorText || '').slice(0, 12000)
            }, '*');
        });
        return explainBtn;
    };

    const getCleanOutputText = (outputNode) => {
        const clone = outputNode.cloneNode(true);
        clone.querySelectorAll('.inj-explain-error-inline, .inj-error-controls').forEach((n) => n.remove());
        return clone.innerText ? clone.innerText.trim() : '';
    };

    const clearCellExplainUi = (cell) => {
        cell.querySelectorAll('.inj-explain-error-inline').forEach((n) => n.remove());
        cell.querySelectorAll('.inj-error-controls').forEach((n) => n.remove());
    };

    const ensureBadge = (cell, index) => {
        cell.style.position = 'relative';
        let badge = cell.querySelector('.inj-badge');
        if (!badge) {
            badge = document.createElement('div');
            badge.className = 'inj-badge';
            badge.style.position = 'absolute';
            badge.style.top = '35px';
            badge.style.left = '30px';
            badge.style.width = '18px';
            badge.style.height = '18px';
            badge.style.borderRadius = '2px';
            badge.style.backgroundColor = '#111111';
            badge.style.color = '#ffffff';
            badge.style.fontSize = '13px';
            badge.style.fontWeight = 'bold';
            badge.style.fontFamily = 'monospace';
            badge.style.display = 'flex';
            badge.style.alignItems = 'center';
            badge.style.justifyContent = 'center';
            badge.style.pointerEvents = 'none';
            badge.style.zIndex = '9999';
            cell.appendChild(badge);
        }
        badge.innerText = String(index + 1);
    };

    const placeInlineNextToErrorType = (outputNode, explainBtn, errorType) => {
        if (!errorType || typeof document.createTreeWalker !== 'function') return false;

        const textNodes = [];
        const walker = document.createTreeWalker(
            outputNode,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: (node) => {
                    if (!node || !node.parentNode) return NodeFilter.FILTER_REJECT;
                    if (node.parentNode.closest && node.parentNode.closest('.inj-explain-error-inline, .inj-error-controls')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return node.nodeValue && node.nodeValue.trim()
                        ? NodeFilter.FILTER_ACCEPT
                        : NodeFilter.FILTER_REJECT;
                }
            }
        );

        let node;
        while ((node = walker.nextNode())) {
            textNodes.push(node);
        }

        const findTextNode = (predicate) => textNodes.find((n) => {
            const txt = String(n.nodeValue || '');
            return predicate(txt);
        });

        const targetNode =
            findTextNode((txt) => txt.includes(errorType) && /traceback/i.test(txt)) ||
            findTextNode((txt) => txt.includes(errorType + ':')) ||
            findTextNode((txt) => txt.includes(errorType));

        if (!targetNode) return false;

        const txt = String(targetNode.nodeValue || '');
        const at = txt.indexOf(errorType);
        if (at < 0) return false;

        const splitAt = at + errorType.length;
        const left = txt.slice(0, splitAt);
        const right = txt.slice(splitAt);

        const inlineHost = document.createElement('span');
        inlineHost.className = 'inj-explain-error-inline';
        inlineHost.dataset.errorType = errorType;
        inlineHost.style.display = 'inline-flex';
        inlineHost.style.alignItems = 'center';
        inlineHost.style.marginLeft = '8px';
        inlineHost.style.verticalAlign = 'middle';
        inlineHost.appendChild(explainBtn);

        const parent = targetNode.parentNode;
        if (!parent) return false;

        parent.insertBefore(document.createTextNode(left), targetNode);
        parent.insertBefore(inlineHost, targetNode);
        if (right) parent.insertBefore(document.createTextNode(right), targetNode);
        parent.removeChild(targetNode);
        return true;
    };

    const ensureExplainButton = (cell, index) => {
        const outputNode = cell.querySelector('.jp-OutputArea-output') || cell.querySelector('.jp-OutputArea');
        if (!outputNode) {
            clearCellExplainUi(cell);
            return;
        }

        const outputText = getCleanOutputText(outputNode);
        if (!outputText || !ERROR_REGEX.test(outputText)) {
            clearCellExplainUi(cell);
            return;
        }

        const errorTypeMatch = outputText.match(ERROR_TYPE_REGEX);
        const errorType = errorTypeMatch ? errorTypeMatch[1] : '';

        let explainBtn = outputNode.querySelector('.inj-explain-error-btn');
        if (!explainBtn) {
            explainBtn = createExplainButton();
        }

        explainBtn.dataset.cellNumber = String(index + 1);
        explainBtn.dataset.errorText = outputText.slice(0, 12000);

        const existingInline = outputNode.querySelector('.inj-explain-error-inline');
        if (existingInline && errorType && existingInline.dataset.errorType !== errorType) {
            existingInline.remove();
        }

        let placedInline = !!outputNode.querySelector('.inj-explain-error-inline');
        if (!placedInline) {
            placedInline = placeInlineNextToErrorType(outputNode, explainBtn, errorType);
        }

        if (!placedInline) {
            let controls = outputNode.querySelector('.inj-error-controls');
            if (!controls) {
                controls = document.createElement('div');
                controls.className = 'inj-error-controls';
                controls.style.display = 'flex';
                controls.style.justifyContent = 'flex-end';
                controls.style.marginBottom = '6px';
                outputNode.prepend(controls);
            }
            if (!controls.contains(explainBtn)) controls.appendChild(explainBtn);
        } else {
            const inlineHost = outputNode.querySelector('.inj-explain-error-inline');
            if (inlineHost && !inlineHost.contains(explainBtn)) inlineHost.appendChild(explainBtn);
            const controls = outputNode.querySelector('.inj-error-controls');
            if (controls) controls.remove();
        }
    };

    const renderCells = () => {
        const cells = Array.from(document.querySelectorAll(CELL_SELECTOR));
        cells.forEach((cell, index) => {
            ensureBadge(cell, index);
            ensureExplainButton(cell, index);
        });
        return cells.length;
    };

    let renderQueued = false;
    const queueRender = () => {
        if (renderQueued) return;
        renderQueued = true;
        window.requestAnimationFrame(() => {
            renderQueued = false;
            renderCells();
        });
    };

    const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
            if (m.type === 'characterData' || m.addedNodes.length > 0 || m.removedNodes.length > 0) {
                queueRender();
                break;
            }
        }
    });

    observer.observe(document.body || document.documentElement, {
        childList: true,
        subtree: true,
        characterData: true
    });
    window.__injCellBadgeObserver = observer;

    return renderCells();
})()
"""

def inject_cell_badges(port, page_target_id):
    """Injects cell index badges by connecting directly to the iframe's CDP target.
    This bypasses cross-origin restrictions by using the iframe's own WebSocket."""
    all_targets = get_all_tabs(port)

    # Find iframe targets that belong to this page (match parent)
    child_iframes = [
        t for t in all_targets
        if t.get('type') == 'iframe' and t.get('parentId') == page_target_id
    ]

    # Fallback: any kaggle-related iframe
    if not child_iframes:
        child_iframes = [
            t for t in all_targets
            if t.get('type') == 'iframe' and 'kaggle' in t.get('url', '').lower()
        ]

    if not child_iframes:
        print("   ⚠️  Cell badges: No editor iframe target found.")
        return

    iframe_ws = child_iframes[0].get('webSocketDebuggerUrl')
    if not iframe_ws:
        print("   ⚠️  Cell badges: Iframe has no WebSocket URL.")
        return

    # Execute badge JS directly inside the iframe context — no cross-origin issue
    result = cdp_evaluate(iframe_ws, CELL_BADGE_JS)
    val = result.get('result', {}).get('result', {}).get('value')

    if isinstance(val, int):
        print(f"   🔢 Cell badges: {val} code cells indexed.")
    elif 'error' in result:
        print(f"   ⚠️  Cell badges error: {result['error']}")
    else:
        print(f"   ⚠️  Cell badges: Unexpected result: {val}")


def is_panel_alive(ws_url):
    """Returns True if the injected panel is still in the DOM of the target tab."""
    result = cdp_evaluate(ws_url, "!!document.getElementById('injected-copilot-panel-wrapper')")
    try:
        return result.get('result', {}).get('result', {}).get('value', False)
    except Exception:
        return False

def monitoring_loop(port, interval):
    # Maps tab_id -> last known url (to detect navigations)
    tab_state = {}  # tab_id: {'url': str}

    while True:
        tabs = filter_notebook_tabs(get_all_tabs(port))
        for tid, info in tabs.items():
            ws_url  = info['webSocketDebuggerUrl']
            raw_url = info.get('url', '')
            cur_url = _normalize_notebook_url(raw_url)

            prev = tab_state.get(tid)

            needs_inject = False
            if prev is None or prev['url'] != cur_url:
                needs_inject = True
            else:
                if not is_panel_alive(ws_url):
                    needs_inject = True

            if needs_inject:
                print(f"💉 Injecting into: {raw_url[:50]}...")
                res = inject_panel(ws_url)
                if 'error' not in res:
                    tab_state[tid] = {'url': cur_url}
                    print(f"   ✅ Panel injected.")
                    inject_cell_badges(port, tid)
                else:
                    print(f"   ⚠️  Injection failed, will retry.")

            # ---- Per-notebook scraping (runs every cycle) ----
            if tid in tab_state:
                profile_manager.scrape_notebook(port, tid, cur_url)

        # Clean up tabs that are no longer open
        open_ids = set(tabs.keys())
        for tid in list(tab_state.keys()):
            if tid not in open_ids:
                del tab_state[tid]

        time.sleep(interval)

# ========== MAIN ==========
# ========== AUTO-RELOAD WATCHER ==========
def auto_reload_watcher():
    """Monitors the script file and restarts it if modified."""
    initial_mtime = os.path.getmtime(__file__)
    while True:
        try:
            time.sleep(1)
            # Check if this file has been modified
            if os.path.getmtime(__file__) > initial_mtime:
                print("\n[RELOAD] Changes detected! Restarting GUI-Craft workspace...")
                python = sys.executable
                os.execl(python, python, *sys.argv)
        except Exception:
            pass

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-inject", action="store_true", help="Run only the design sandbox server")
    parser.add_argument("--port", type=int, default=DEFAULT_DEBUG_PORT)
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reloading")
    args = parser.parse_args()

    # Start Auto-Reload Watcher
    if not args.no_reload:
        reloader = threading.Thread(target=auto_reload_watcher, daemon=True)
        reloader.start()
        print("[DEBUG] Debug Mode: Active (Auto-reloading on save)")

    # Start Server
    server = threading.Thread(target=lambda: HTTPServer(('', PROXY_PORT), CraftingHandler).serve_forever(), daemon=True)
    server.start()
    print(f"[SERVER] GUI Craft Workspace: http://localhost:{PROXY_PORT}")

    if not args.no_inject:
        print(f"[CDP] Monitoring Chrome on port {args.port} for injection...")
        try: monitoring_loop(args.port, 2.0)
        except KeyboardInterrupt: pass
    else:
        print("[INFO] Sandbox mode active. Injection disabled.")
        try: 
            while True: time.sleep(1)
        except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
