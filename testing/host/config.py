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
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")
LLM_MODEL = CEREBRAS_MODEL

# LLM provider: cerebras (default) | google (Google AI Studio / Gemini)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "cerebras").strip().lower()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", "")).strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()

try:
    from .llm_provider import (
        resolve_gemini_model_id,
        gemini_free_tier_limits,
        normalize_provider,
        cerebras_rate_limits,
    )
except Exception:
    from llm_provider import (
        resolve_gemini_model_id,
        gemini_free_tier_limits,
        normalize_provider,
        cerebras_rate_limits,
    )

LLM_PROVIDER = normalize_provider(LLM_PROVIDER)
if LLM_PROVIDER == "google":
    GEMINI_MODEL = resolve_gemini_model_id(GEMINI_MODEL)
    LLM_MODEL = GEMINI_MODEL

# ReAct / agentic: server master switch (dashboard toggle + Agentic mode still required).
_LLM_AGENTIC_RAW = os.environ.get(
    "LLM_AGENTIC_ENABLED",
    os.environ.get("LLM_BROWSER_TOOLS", "0"),
).strip().lower()
LLM_AGENTIC_ENABLED = _LLM_AGENTIC_RAW in ("1", "true", "yes")
# Back-compat alias
LLM_BROWSER_TOOLS = LLM_AGENTIC_ENABLED
LLM_REACT_MAX_ROUNDS = int(os.environ.get("LLM_REACT_MAX_ROUNDS", "15"))

# AIML API (disabled until project finalized)
# AIML_API_KEY = os.environ.get("AIML_API_KEY", "").strip()
# AIML_API_BASE_URL = os.environ.get("AIML_API_BASE_URL", "https://api.aimlapi.com/v1").strip()
# AIML_MODEL = os.environ.get("AIML_MODEL", "x-ai/grok-4-1-fast-reasoning").strip()

KAGGLE_USERNAME = os.environ.get("KAGGLE_USERNAME", "").strip()
KAGGLE_KEY = os.environ.get("KAGGLE_KEY", "").strip()

TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", os.environ.get("CEREBRAS_TEMPERATURE", "0.5")))
TOP_P = float(os.environ.get("LLM_TOP_P", os.environ.get("CEREBRAS_TOP_P", "1.0")))
DATA_ROOT = Path(__file__).parent / "data"
CHAT_MEMORY_DB = DATA_ROOT / "sessions" / "chat_history.sqlite3"
SCRAPED_DIR = DATA_ROOT / "notebooks"
HASHES_PATH = DATA_ROOT / "meta" / "hashes.json"
KERNEL_METADATA_DIR = DATA_ROOT / "meta" / "kernel_metadata"
KERNEL_SLUG_INDEX_PATH = DATA_ROOT / "meta" / "kernel_slug_index.json"
EXECUTION_STATE_PATH = DATA_ROOT / "meta" / "execution_state.json"
LOG_PATH = DATA_ROOT / "logs" / "host.log"
RATE_LIMIT_TRACKER = DATA_ROOT / "meta" / "rate_limit_tracker.json"
BOT_COMMANDS_PATH = DATA_ROOT / "meta" / "bot_commands.jsonl"
BOT_RESULTS_PATH = DATA_ROOT / "meta" / "bot_results.jsonl"
DB_TIMEOUT_SECONDS = 10
MAX_HISTORY_MESSAGES = 24  # SQLite + UI display
MAX_HISTORY_CHARS_PER_MSG = int(os.environ.get("MAX_HISTORY_CHARS_PER_MSG", "1000"))
# Context packing: "full" sends every cell with metadata; "intent" uses mode-specific slices.
CONTEXT_PACK_MODE = os.environ.get("CONTEXT_PACK_MODE", "full").strip().lower()
if CONTEXT_PACK_MODE not in ("full", "intent"):
    CONTEXT_PACK_MODE = "full"
if "MAX_HISTORY_MESSAGES_API" in os.environ:
    MAX_HISTORY_MESSAGES_API = int(os.environ["MAX_HISTORY_MESSAGES_API"])
elif CONTEXT_PACK_MODE == "full":
    MAX_HISTORY_MESSAGES_API = 3
else:
    MAX_HISTORY_MESSAGES_API = 10
if "MAX_INPUT_TOKENS" in os.environ:
    MAX_INPUT_TOKENS = int(os.environ["MAX_INPUT_TOKENS"])
elif CONTEXT_PACK_MODE == "full":
    MAX_INPUT_TOKENS = int(os.environ.get("LLM_MAX_INPUT_TOKENS", "7000"))
else:
    MAX_INPUT_TOKENS = 7000
CHARS_PER_TOKEN_ESTIMATE = int(os.environ.get("CHARS_PER_TOKEN_ESTIMATE", "4"))
MAX_CONTEXT_CHARS = 1800
if "MAX_NOTEBOOK_CONTEXT_CHARS" in os.environ:
    MAX_NOTEBOOK_CONTEXT_CHARS = int(os.environ["MAX_NOTEBOOK_CONTEXT_CHARS"])
elif CONTEXT_PACK_MODE == "full":
    MAX_NOTEBOOK_CONTEXT_CHARS = int(os.environ.get("MAX_NOTEBOOK_CONTEXT_CHARS", "6000"))
else:
    MAX_NOTEBOOK_CONTEXT_CHARS = 6000
# Full-mode notebook char cap. 0 = include every cell (large-context LLMs).
if "MAX_FULL_NOTEBOOK_CONTEXT_CHARS" in os.environ:
    MAX_FULL_NOTEBOOK_CONTEXT_CHARS = int(os.environ["MAX_FULL_NOTEBOOK_CONTEXT_CHARS"])
elif CONTEXT_PACK_MODE == "full":
    MAX_FULL_NOTEBOOK_CONTEXT_CHARS = int(os.environ.get("MAX_FULL_NOTEBOOK_CONTEXT_CHARS", "30000"))
else:
    MAX_FULL_NOTEBOOK_CONTEXT_CHARS = 0
# Local TPM preflight (on by default when an LLM API key is set).
TPM_PREFLIGHT_RATIO = float(os.environ.get("TPM_PREFLIGHT_RATIO", "0.85"))
_ENABLE_PREFLIGHT_DEFAULT = "1" if (CEREBRAS_API_KEY or GEMINI_API_KEY) else "0"
ENABLE_TPM_PREFLIGHT = os.environ.get(
    "ENABLE_TPM_PREFLIGHT",
    _ENABLE_PREFLIGHT_DEFAULT,
).strip().lower() in ("1", "true", "yes")
MAX_PROFILE_FACTS = 12
SYMBOL_CONTEXT_ENABLED = os.environ.get("SYMBOL_CONTEXT_ENABLED", "1").strip().lower() in ("1", "true", "yes")
MAX_SYMBOL_SNIPPET_CHARS = int(os.environ.get("MAX_SYMBOL_SNIPPET_CHARS", "400"))
MAX_SYMBOL_DEPTH = int(os.environ.get("MAX_SYMBOL_DEPTH", "2"))
if "MAX_CELL_OUTPUT_CHARS" in os.environ:
    MAX_CELL_OUTPUT_CHARS = int(os.environ["MAX_CELL_OUTPUT_CHARS"])
elif CONTEXT_PACK_MODE == "full":
    MAX_CELL_OUTPUT_CHARS = int(os.environ.get("MAX_CELL_OUTPUT_CHARS", "2500"))
else:
    MAX_CELL_OUTPUT_CHARS = 2500
ALLOWED_MODES = {"ask", "code", "agentic"}

# Cerebras prefix caching: freeze session-start notebook in system; send live deltas on current turn only.
_cerebras_prompt_cache = os.environ.get("CEREBRAS_PROMPT_CACHE", "1").strip().lower() in ("1", "true", "yes")
_static_cache_default = "1" if (_cerebras_prompt_cache and LLM_PROVIDER == "cerebras") else "0"
CEREBRAS_STATIC_NOTEBOOK_CACHE = os.environ.get(
    "CEREBRAS_STATIC_NOTEBOOK_CACHE",
    _static_cache_default,
).strip().lower() in ("1", "true", "yes")
# When 1, SQLite history + baselines are scoped per mode (ask/code/agentic) under the same UI session id.
CHAT_SESSION_PER_MODE = os.environ.get("CHAT_SESSION_PER_MODE", "0").strip().lower() in ("1", "true", "yes")

# Compact frozen baseline for Cerebras prefix cache (outputs truncated; full state via deltas/tools).
BASELINE_MAX_CELL_INPUT_CHARS = int(os.environ.get("BASELINE_MAX_CELL_INPUT_CHARS", "1500"))
BASELINE_MAX_CELL_OUTPUT_CHARS = int(os.environ.get("BASELINE_MAX_CELL_OUTPUT_CHARS", "350"))
BASELINE_MAX_TOTAL_CHARS = int(os.environ.get("BASELINE_MAX_TOTAL_CHARS", "10000"))
MAX_TOOL_RESULT_CHARS = int(os.environ.get("MAX_TOOL_RESULT_CHARS", "2200"))
QUERY_TOOL_MAX_OUTPUT_CHARS = int(os.environ.get("QUERY_TOOL_MAX_OUTPUT_CHARS", "6000"))
QUERY_TOOL_MAX_INPUT_CHARS = int(os.environ.get("QUERY_TOOL_MAX_INPUT_CHARS", "8000"))
TOKEN_USAGE_LOG_PATH = DATA_ROOT / "meta" / "token_usage.jsonl"

if CEREBRAS_STATIC_NOTEBOOK_CACHE:
    if "MAX_FULL_NOTEBOOK_CONTEXT_CHARS" not in os.environ:
        MAX_FULL_NOTEBOOK_CONTEXT_CHARS = min(int(MAX_FULL_NOTEBOOK_CONTEXT_CHARS or 0) or 12000, 12000)
    if "MAX_HISTORY_MESSAGES_API" not in os.environ:
        MAX_HISTORY_MESSAGES_API = min(int(MAX_HISTORY_MESSAGES_API), 2)
    if "MAX_INPUT_TOKENS" not in os.environ and "LLM_MAX_INPUT_TOKENS" not in os.environ:
        MAX_INPUT_TOKENS = min(int(MAX_INPUT_TOKENS), 5500)

# Free-tier limits (provider-specific defaults).
if LLM_PROVIDER == "google":
    _glim = gemini_free_tier_limits(GEMINI_MODEL)
    TPM_LIMIT = int(os.environ.get("GEMINI_TPM_LIMIT", str(_glim["tpm"])))
    RPM_LIMIT = int(os.environ.get("GEMINI_RPM_LIMIT", str(_glim["rpm"])))
    RPD_LIMIT = int(os.environ.get("GEMINI_RPD_LIMIT", str(_glim["rpd"])))
    TPH_LIMIT = int(os.environ.get("GEMINI_TPH_LIMIT", str(_glim["tph"])))
    RPH_LIMIT = int(os.environ.get("GEMINI_RPH_LIMIT", str(_glim["rph"])))
    TPD_LIMIT = int(os.environ.get("GEMINI_TPD_LIMIT", str(TPM_LIMIT * 60 * 24)))
else:
    _clim = cerebras_rate_limits()
    TPM_LIMIT = _clim["tpm"]
    TPH_LIMIT = int(os.environ.get("CEREBRAS_TPH_LIMIT", "1000000"))
    TPD_LIMIT = int(os.environ.get("CEREBRAS_TPD_LIMIT", "1000000"))
    RPM_LIMIT = _clim["rpm"]  # hardcoded 5 req/min — see llm_provider.CEREBRAS_RPM_HARD_LIMIT
    RPH_LIMIT = _clim["rph"]
    RPD_LIMIT = _clim["rpd"]

# Threading locks
_HASHES_LOCK = threading.Lock()
_EXECUTION_STATE_LOCK = threading.Lock()
_SEND_LOCK = threading.Lock()
_ACTIVE_STREAMS = {}
_ACTIVE_STREAMS_LOCK = threading.Lock()
_RATE_LOCK = threading.Lock()
_BOT_STATE_LOCK = threading.Lock()

_CEREBRAS_CLIENT = Cerebras(api_key=CEREBRAS_API_KEY) if CEREBRAS_API_KEY else None
_LLM_CLIENT = None

if LLM_PROVIDER == "google" and GEMINI_API_KEY:
    try:
        from .google_ai import create_google_client
    except Exception:
        from google_ai import create_google_client
    _GOOGLE_CLIENT = create_google_client()
    if _GOOGLE_CLIENT is not None:
        _LLM_CLIENT = _GOOGLE_CLIENT
        LLM_MODEL = GEMINI_MODEL
elif CEREBRAS_API_KEY:
    _LLM_CLIENT = _CEREBRAS_CLIENT
    LLM_MODEL = CEREBRAS_MODEL

# AIML client (disabled until project finalized)
# try:
#     from .aimlapi import create_aiml_client
# except Exception:
#     from aimlapi import create_aiml_client
# _LLM_CLIENT = create_aiml_client()


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

