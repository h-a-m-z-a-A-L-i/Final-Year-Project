import os
import sys
import threading
from pathlib import Path
from datetime import timedelta
from cerebras.cloud.sdk import Cerebras


def _load_dotenv(env_path: Path):
    if not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


# Load .env from workspace root
_load_dotenv(Path(__file__).resolve().parents[2] / ".env")

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "").strip()
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "llama3.1-8b")
TEMPERATURE = float(os.environ.get("CEREBRAS_TEMPERATURE", "0.5"))
TOP_P = float(os.environ.get("CEREBRAS_TOP_P", "1.0"))
DATA_ROOT = Path(__file__).parent / "data"
CHAT_MEMORY_DB = DATA_ROOT / "sessions" / "chat_history.sqlite3"
SCRAPED_DIR = DATA_ROOT / "notebooks"
HASHES_PATH = DATA_ROOT / "meta" / "hashes.json"
EXECUTION_STATE_PATH = DATA_ROOT / "meta" / "execution_state.json"
LOG_PATH = DATA_ROOT / "logs" / "host.log"
RATE_LIMIT_TRACKER = DATA_ROOT / "meta" / "rate_limit_tracker.json"
BOT_COMMANDS_PATH = DATA_ROOT / "meta" / "bot_commands.jsonl"
BOT_RESULTS_PATH = DATA_ROOT / "meta" / "bot_results.jsonl"
DB_TIMEOUT_SECONDS = 10
MAX_HISTORY_MESSAGES = 24  # SQLite + UI display
MAX_HISTORY_MESSAGES_API = int(os.environ.get("MAX_HISTORY_MESSAGES_API", "10"))
MAX_HISTORY_CHARS_PER_MSG = int(os.environ.get("MAX_HISTORY_CHARS_PER_MSG", "1000"))
MAX_INPUT_TOKENS = int(os.environ.get("MAX_INPUT_TOKENS", "7000"))
CHARS_PER_TOKEN_ESTIMATE = int(os.environ.get("CHARS_PER_TOKEN_ESTIMATE", "4"))
MAX_CONTEXT_CHARS = 1800
MAX_NOTEBOOK_CONTEXT_CHARS = int(os.environ.get("MAX_NOTEBOOK_CONTEXT_CHARS", "6000"))
MAX_PROFILE_FACTS = 12
SYMBOL_CONTEXT_ENABLED = os.environ.get("SYMBOL_CONTEXT_ENABLED", "1").strip().lower() in ("1", "true", "yes")
MAX_SYMBOL_SNIPPET_CHARS = int(os.environ.get("MAX_SYMBOL_SNIPPET_CHARS", "400"))
MAX_SYMBOL_DEPTH = int(os.environ.get("MAX_SYMBOL_DEPTH", "2"))
MAX_CELL_OUTPUT_CHARS = int(os.environ.get("MAX_CELL_OUTPUT_CHARS", "2500"))
ALLOWED_MODES = {"ask", "code"}

# Free-tier limits.
TPM_LIMIT = int(os.environ.get("CEREBRAS_TPM_LIMIT", "60000"))
TPH_LIMIT = int(os.environ.get("CEREBRAS_TPH_LIMIT", "1000000"))
TPD_LIMIT = int(os.environ.get("CEREBRAS_TPD_LIMIT", "1000000"))
RPM_LIMIT = int(os.environ.get("CEREBRAS_RPM_LIMIT", "30"))
RPH_LIMIT = int(os.environ.get("CEREBRAS_RPH_LIMIT", "900"))
RPD_LIMIT = int(os.environ.get("CEREBRAS_RPD_LIMIT", "14400"))

# Threading locks
_HASHES_LOCK = threading.Lock()
_EXECUTION_STATE_LOCK = threading.Lock()
_SEND_LOCK = threading.Lock()
_ACTIVE_STREAMS = {}
_ACTIVE_STREAMS_LOCK = threading.Lock()
_RATE_LOCK = threading.Lock()
_BOT_STATE_LOCK = threading.Lock()

# Cerebras client instance (None when no key provided)
_CEREBRAS_CLIENT = Cerebras(api_key=CEREBRAS_API_KEY) if CEREBRAS_API_KEY else None


def ensure_dirs():
    # ensure data directories exist
    for p in (DATA_ROOT, DATA_ROOT / 'meta', DATA_ROOT / 'logs', DATA_ROOT / 'notebooks', DATA_ROOT / 'sessions'):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


ensure_dirs()


def log(msg: str):
    from datetime import datetime, timezone
    try:
        line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}"
        print(line, file=sys.stderr, flush=True)
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open('a', encoding='utf-8') as f:
                f.write(line + "\n")
        except Exception:
            pass
    except Exception:
        try:
            print(msg, file=sys.stderr, flush=True)
        except Exception:
            pass

