import json, struct, sys, threading, re, sqlite3, os, ast, uuid
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse
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

# ========== CONFIGURATION ==========
_load_dotenv(Path(__file__).resolve().parents[2] / ".env")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "").strip()
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "qwen-3-235b-a22b-instruct-2507")
TEMPERATURE = float(os.environ.get("CEREBRAS_TEMPERATURE", "0.5"))
TOP_P = float(os.environ.get("CEREBRAS_TOP_P", "1.0"))
DATA_ROOT = Path(__file__).parent / "data"
CHAT_MEMORY_DB = DATA_ROOT / "sessions" / "chat_history.sqlite3"
SCRAPED_DIR = DATA_ROOT / "notebooks"
HASHES_PATH = DATA_ROOT / "meta" / "hashes.json"
LOG_PATH = DATA_ROOT / "logs" / "host.log"
RATE_LIMIT_TRACKER = DATA_ROOT / "meta" / "rate_limit_tracker.json"
DB_TIMEOUT_SECONDS = 10
MAX_HISTORY_MESSAGES = 24
MAX_CONTEXT_CHARS = 1800
MAX_PROFILE_FACTS = 12
ALLOWED_MODES = {"simple", "explain_error", "dependency", "code_review", "explain_code"}

# Free-tier limits.
TPM_LIMIT = int(os.environ.get("CEREBRAS_TPM_LIMIT", "60000"))
TPH_LIMIT = int(os.environ.get("CEREBRAS_TPH_LIMIT", "1000000"))
TPD_LIMIT = int(os.environ.get("CEREBRAS_TPD_LIMIT", "1000000"))
RPM_LIMIT = int(os.environ.get("CEREBRAS_RPM_LIMIT", "30"))
RPH_LIMIT = int(os.environ.get("CEREBRAS_RPH_LIMIT", "900"))
RPD_LIMIT = int(os.environ.get("CEREBRAS_RPD_LIMIT", "14400"))

_HASHES_LOCK = threading.Lock()
_SEND_LOCK = threading.Lock()
_ACTIVE_STREAMS = {}
_ACTIVE_STREAMS_LOCK = threading.Lock()
_RATE_LOCK = threading.Lock()
_CEREBRAS_CLIENT = Cerebras(api_key=CEREBRAS_API_KEY) if CEREBRAS_API_KEY else None
# Set up dependency mode path with fallbacks.
_WS_ROOT = Path(__file__).resolve().parents[2]
_DEP_CANDIDATES = [
    _WS_ROOT / "DB" / "dependency_mode",
    _WS_ROOT / "database" / "dependency_mode",
    _WS_ROOT / "dependency_mode",
]
for _cand in _DEP_CANDIDATES:
    if _cand.is_dir():
        _cand_str = str(_cand)
        if _cand_str not in sys.path:
            sys.path.insert(0, _cand_str)

try:
    from dependency_tracker import DependencyTracker
    from context_builder import ContextBuilder
    _DEP_AVAILABLE = True
    _DEP_FALLBACK = False
except ImportError:
    _DEP_FALLBACK = True

    class DependencyTracker:
        """Fallback dependency tracker based on symbol define/use analysis."""
        def __init__(self):
            self._symbol_table = {}
            self._deps = {}
            self._reverse = {}

        @staticmethod
        def _collect_target_names(target, out_set):
            if isinstance(target, ast.Name):
                out_set.add(target.id)
                return
            if isinstance(target, (ast.Tuple, ast.List)):
                for elt in target.elts:
                    DependencyTracker._collect_target_names(elt, out_set)

        def _parse_symbols(self, code: str):
            defines, uses = set(), set()
            if not code.strip():
                return defines, uses
            try:
                tree = ast.parse(code)
                builtin_names = set(dir(__builtins__))

                class SymbolVisitor(ast.NodeVisitor):
                    def __init__(self, defs_out, uses_out, builtins):
                        self.defines = defs_out
                        self.uses = uses_out
                        self.builtins = builtins
                        self.scope_stack = [set()]

                    def _add_local_define(self, name):
                        if not name:
                            return
                        self.scope_stack[-1].add(name)

                    def _is_local(self, name):
                        return any(name in scope for scope in reversed(self.scope_stack))

                    def visit_Assign(self, node):
                        for t in node.targets:
                            DependencyTracker._collect_target_names(t, self.defines)
                            names = set()
                            DependencyTracker._collect_target_names(t, names)
                            for n in names:
                                self._add_local_define(n)
                        self.generic_visit(node.value)

                    def visit_AnnAssign(self, node):
                        names = set()
                        DependencyTracker._collect_target_names(node.target, names)
                        self.defines.update(names)
                        for n in names:
                            self._add_local_define(n)
                        if node.value:
                            self.generic_visit(node.value)

                    def visit_AugAssign(self, node):
                        names = set()
                        DependencyTracker._collect_target_names(node.target, names)
                        self.defines.update(names)
                        for n in names:
                            self._add_local_define(n)
                        self.generic_visit(node.value)

                    def visit_NamedExpr(self, node):
                        names = set()
                        DependencyTracker._collect_target_names(node.target, names)
                        self.defines.update(names)
                        for n in names:
                            self._add_local_define(n)
                        self.generic_visit(node.value)

                    def visit_For(self, node):
                        names = set()
                        DependencyTracker._collect_target_names(node.target, names)
                        self.defines.update(names)
                        for n in names:
                            self._add_local_define(n)
                        self.generic_visit(node.iter)
                        for stmt in node.body:
                            self.visit(stmt)
                        for stmt in node.orelse:
                            self.visit(stmt)

                    def visit_AsyncFor(self, node):
                        self.visit_For(node)

                    def visit_With(self, node):
                        for item in node.items:
                            self.visit(item.context_expr)
                            if item.optional_vars is not None:
                                names = set()
                                DependencyTracker._collect_target_names(item.optional_vars, names)
                                self.defines.update(names)
                                for n in names:
                                    self._add_local_define(n)
                        for stmt in node.body:
                            self.visit(stmt)

                    def visit_AsyncWith(self, node):
                        self.visit_With(node)

                    def visit_ExceptHandler(self, node):
                        if isinstance(node.name, str):
                            self.defines.add(node.name)
                            self._add_local_define(node.name)
                        for stmt in node.body:
                            self.visit(stmt)

                    def visit_Import(self, node):
                        for alias in node.names:
                            name = alias.asname or alias.name.split('.')[0]
                            self.defines.add(name)
                            self._add_local_define(name)

                    def visit_ImportFrom(self, node):
                        for alias in node.names:
                            name = alias.asname or alias.name.split('.')[0]
                            self.defines.add(name)
                            self._add_local_define(name)

                    def _visit_scoped_body(self, args, body):
                        self.scope_stack.append(set())
                        for arg in getattr(args, 'posonlyargs', []):
                            self._add_local_define(arg.arg)
                        for arg in getattr(args, 'args', []):
                            self._add_local_define(arg.arg)
                        for arg in getattr(args, 'kwonlyargs', []):
                            self._add_local_define(arg.arg)
                        vararg = getattr(args, 'vararg', None)
                        if vararg:
                            self._add_local_define(vararg.arg)
                        kwarg = getattr(args, 'kwarg', None)
                        if kwarg:
                            self._add_local_define(kwarg.arg)
                        for stmt in body:
                            self.visit(stmt)
                        self.scope_stack.pop()

                    def visit_FunctionDef(self, node):
                        self.defines.add(node.name)
                        self._add_local_define(node.name)
                        self._visit_scoped_body(node.args, node.body)

                    def visit_AsyncFunctionDef(self, node):
                        self.visit_FunctionDef(node)

                    def visit_ClassDef(self, node):
                        self.defines.add(node.name)
                        self._add_local_define(node.name)
                        self.scope_stack.append(set())
                        for base in node.bases:
                            self.visit(base)
                        for dec in node.decorator_list:
                            self.visit(dec)
                        for stmt in node.body:
                            self.visit(stmt)
                        self.scope_stack.pop()

                    def visit_Name(self, node):
                        if isinstance(node.ctx, ast.Load):
                            if node.id not in self.builtins and not self._is_local(node.id):
                                self.uses.add(node.id)

                SymbolVisitor(defines, uses, builtin_names).visit(tree)
            except SyntaxError:
                defines.update(re.findall(r'^(\w+)\s*=', code, re.M))
            return defines, uses

        def update_cell(self, cell_id: int, code: str):
            defines, uses = self._parse_symbols(code or "")
            self._symbol_table[cell_id] = {"defines": defines, "uses": uses}
            self._recompute_graph()

        def _recompute_graph(self):
            cell_ids = list(self._symbol_table.keys())
            try:
                ordered_ids = sorted(cell_ids, key=lambda x: int(x))
            except Exception:
                ordered_ids = sorted(cell_ids, key=lambda x: str(x))

            pos_by_id = {cid: i for i, cid in enumerate(ordered_ids)}
            define_sites = {}
            for cid, syms in self._symbol_table.items():
                for name in syms["defines"]:
                    define_sites.setdefault(name, []).append(cid)

            for name, ids in define_sites.items():
                define_sites[name] = sorted(ids, key=lambda x: pos_by_id.get(x, 10**9))

            deps = {}
            for idx in ordered_ids:
                used = self._symbol_table[idx]["uses"]
                linked = set()
                idx_pos = pos_by_id.get(idx, -1)

                for symbol in used:
                    candidates = [cid for cid in define_sites.get(symbol, []) if cid != idx]
                    if not candidates:
                        continue

                    prior = [cid for cid in candidates if pos_by_id.get(cid, -1) < idx_pos]
                    if prior:
                        linked.add(prior[-1])
                    else:
                        linked.add(candidates[-1])

                deps[idx] = sorted(linked, key=lambda x: pos_by_id.get(x, 10**9))
            self._deps = deps
            self.update_all_reverse_dependencies()

        def update_all_reverse_dependencies(self):
            reverse = {idx: [] for idx in self._symbol_table.keys()}
            for idx, dep_list in self._deps.items():
                for dep in dep_list:
                    reverse.setdefault(dep, []).append(idx)
            self._reverse = {k: sorted(v) for k, v in reverse.items()}

        def get_dependencies(self, cell_id: int, transitive: bool = False):
            direct = self._deps.get(cell_id, [])
            if not transitive:
                return direct
            seen = set(direct)
            stack = list(direct)
            while stack:
                node = stack.pop()
                for nxt in self._deps.get(node, []):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return sorted(seen)

        def get_reverse_dependencies(self, cell_id: int):
            return self._reverse.get(cell_id, [])

    class ContextBuilder:
        """Fallback context builder with same surface used by host.py."""
        def __init__(self, tracker, cells_data):
            self.tracker = tracker
            self.cells = cells_data

        def get_cell_context(self, cell_id: int) -> str:
            cell = self.cells.get(cell_id)
            if not cell:
                return ""
            deps = self.tracker.get_dependencies(cell_id, transitive=False)
            rev = self.tracker.get_reverse_dependencies(cell_id)
            lines = [f"Cell {cell_id}", f"Code:\n{cell.get('code', '')}"]
            if cell.get("output"):
                lines.append(f"Output:\n{cell.get('output', '')}")
            lines.append(f"Depends on cells: {deps}")
            lines.append(f"Used by cells: {rev}")
            return "\n\n".join(lines)

        def get_full_context(self) -> str:
            parts = []
            for idx in sorted(self.cells.keys()):
                c = self.cells[idx]
                parts.append(f"Cell {idx}:\n{c.get('code', '')}")
            return "\n\n".join(parts)

    _DEP_AVAILABLE = True

def _normalized_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))
    except Exception:
        return raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")

def _history_url_key(raw_url: str) -> str:
    """Return a stable URL key for history isolation; empty means invalid/missing URL."""
    return _normalized_url(raw_url or "")

def _extract_user_profile_facts(prompt: str) -> dict:
    """Extract small, stable user facts from plain-text prompts."""
    text = str(prompt or "").strip()
    if not text:
        return {}

    facts = {}
    name_match = re.search(r"\bmy\s+name\s+is\s+([A-Za-z][A-Za-z\-']*(?:\s+[A-Za-z][A-Za-z\-']*){0,3})\b", text, re.IGNORECASE)
    if name_match:
        raw_name = re.sub(r"\s+", " ", name_match.group(1)).strip()
        if raw_name:
            facts["name"] = raw_name
    return facts

def _build_profile_memory_context(facts: dict) -> str:
    if not facts:
        return ""
    items = []
    if facts.get("name"):
        items.append(f"- user_name: {facts['name']}")
    if not items:
        return ""
    return "User Profile Memory:\n" + "\n".join(items)

def _extract_cell_number(prompt: str):
    """Extract a referenced cell number from free-form prompt text.

    Supported examples:
    - cell 1
    - cell1
    - cell#1
    - (cell 1)
    - [cell 1]
    """
    text = str(prompt or "")
    m = re.search(r"(?:\(|\[)?\s*cell\s*#?\s*(\d+)\s*(?:\)|\])?", text, re.IGNORECASE)
    if not m:
        return None
    try:
        n = int(m.group(1))
        return n if n > 0 else None
    except Exception:
        return None

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}"
    print(line, file=sys.stderr, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def read_msg():
    raw = sys.stdin.buffer.read(4)
    if not raw:
        return None
    if len(raw) < 4:
        raise ValueError("Incomplete native message length")
    length = struct.unpack("<I", raw)[0]
    payload = sys.stdin.buffer.read(length)
    if len(payload) < length:
        raise ValueError("Incomplete native message payload")
    if not payload:
        return {}
    return json.loads(payload)

def send_msg(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    with _SEND_LOCK:
        sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
        sys.stdout.buffer.flush()

def _signal_remote_stop(session_id: str):
    # No remote stop endpoint is needed with direct SDK streaming.
    return

def _close_stream_handle(stream):
    if stream is None:
        return
    for attr in ("close", "aclose", "cancel"):
        try:
            closer = getattr(stream, attr, None)
            if callable(closer):
                closer()
                return
        except Exception:
            continue

def _stop_active_stream(state: dict | None):
    if not state:
        return
    state["stopped"] = True
    _close_stream_handle(state.get("stream"))

def _chunk_text_from_event(event) -> str:
    def _extract_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            out = []
            for part in value:
                if isinstance(part, dict):
                    t = part.get("text") or part.get("content") or ""
                else:
                    t = getattr(part, "text", None) or getattr(part, "content", "")
                t = _extract_text(t)
                if t:
                    out.append(t)
            return "".join(out)
        if isinstance(value, dict):
            return _extract_text(value.get("text") or value.get("content") or "")
        return str(getattr(value, "text", None) or getattr(value, "content", "") or "")

    try:
        choices = getattr(event, "choices", None) or []
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            return ""
        return _extract_text(getattr(delta, "content", None))
    except Exception:
        return ""

def _final_text_from_response(response) -> str:
    def _extract_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            out = []
            for part in value:
                if isinstance(part, dict):
                    t = part.get("text") or part.get("content") or ""
                else:
                    t = getattr(part, "text", None) or getattr(part, "content", "")
                t = _extract_text(t)
                if t:
                    out.append(t)
            return "".join(out)
        if isinstance(value, dict):
            return _extract_text(value.get("text") or value.get("content") or "")
        return str(getattr(value, "text", None) or getattr(value, "content", "") or "")

    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            if hasattr(response, "model_dump"):
                dumped = response.model_dump()
                d_choices = dumped.get("choices") or []
                if d_choices:
                    msg = d_choices[0].get("message") or {}
                    return _extract_text(msg.get("content"))
            return ""
        message = getattr(choices[0], "message", None)
        if message is not None:
            text = _extract_text(getattr(message, "content", None))
            if text:
                return text
        if hasattr(response, "model_dump"):
            dumped = response.model_dump()
            d_choices = dumped.get("choices") or []
            if d_choices:
                msg = d_choices[0].get("message") or {}
                return _extract_text(msg.get("content"))
        return ""
    except Exception:
        return ""

def _load_rate_tracker() -> dict:
    if not RATE_LIMIT_TRACKER.exists():
        return {"events": []}
    try:
        data = json.loads(RATE_LIMIT_TRACKER.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"events": []}
        # Backward compatibility for older tracker shape.
        legacy = data.get("requests", []) if isinstance(data.get("requests", []), list) else []
        events = data.get("events", []) if isinstance(data.get("events", []), list) else []
        if legacy and not events:
            for item in legacy:
                ts = item.get("timestamp")
                if ts:
                    events.append({
                        "id": str(uuid.uuid4()),
                        "timestamp": ts,
                        "tokens": int(item.get("tokens", 0) or 0),
                        "requests": int(item.get("requests", 1) or 1),
                    })
        data["events"] = events
        return data
    except Exception:
        return {"events": []}

def _save_rate_tracker(data: dict):
    _atomic_write_json(RATE_LIMIT_TRACKER, data)

def _prune_rate_tracker(data: dict) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    pruned = []
    for event in data.get("events", []):
        try:
            ts = datetime.fromisoformat(str(event.get("timestamp", "")))
        except Exception:
            continue
        if ts >= cutoff:
            pruned.append(event)
    data["events"] = pruned
    return data

def _record_request_attempt(attempt_id: str):
    with _RATE_LOCK:
        tracker = _prune_rate_tracker(_load_rate_tracker())
        tracker.setdefault("events", []).append({
            "id": attempt_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tokens": 0,
            "requests": 1,
        })
        _save_rate_tracker(tracker)

def _finalize_request_attempt(attempt_id: str, tokens: int):
    with _RATE_LOCK:
        tracker = _prune_rate_tracker(_load_rate_tracker())
        for event in reversed(tracker.get("events", [])):
            if event.get("id") == attempt_id:
                event["tokens"] = int(tokens)
                break
        _save_rate_tracker(tracker)

def _rate_usage(events: list):
    now = datetime.now(timezone.utc)
    one_min = now - timedelta(minutes=1)
    one_hour = now - timedelta(hours=1)
    one_day = now - timedelta(hours=24)

    tpm = rpm = tph = rph = tpd = rpd = 0
    for event in events:
        try:
            ts = datetime.fromisoformat(str(event.get("timestamp", "")))
        except Exception:
            continue
        tokens = int(event.get("tokens", 0) or 0)
        reqs = int(event.get("requests", 1) or 1)
        if ts >= one_min:
            tpm += tokens
            rpm += reqs
        if ts >= one_hour:
            tph += tokens
            rph += reqs
        if ts >= one_day:
            tpd += tokens
            rpd += reqs
    return tpm, rpm, tph, rph, tpd, rpd

def _wait_for_request_slot():
    while True:
        with _RATE_LOCK:
            tracker = _prune_rate_tracker(_load_rate_tracker())
            events = tracker.get("events", [])
            now = datetime.now(timezone.utc)
            one_min = now - timedelta(minutes=1)
            one_hour = now - timedelta(hours=1)
            one_day = now - timedelta(hours=24)

            rpm = sum(1 for e in events if datetime.fromisoformat(str(e.get("timestamp", ""))) >= one_min)
            rph = sum(1 for e in events if datetime.fromisoformat(str(e.get("timestamp", ""))) >= one_hour)
            rpd = sum(1 for e in events if datetime.fromisoformat(str(e.get("timestamp", ""))) >= one_day)

            if rpm < RPM_LIMIT and rph < RPH_LIMIT and rpd < RPD_LIMIT:
                _save_rate_tracker(tracker)
                return

            waits = []
            if rpm >= RPM_LIMIT:
                oldest = min(datetime.fromisoformat(e["timestamp"]) for e in events if datetime.fromisoformat(e["timestamp"]) >= one_min)
                waits.append((oldest + timedelta(minutes=1) - now).total_seconds())
            if rph >= RPH_LIMIT:
                oldest = min(datetime.fromisoformat(e["timestamp"]) for e in events if datetime.fromisoformat(e["timestamp"]) >= one_hour)
                waits.append((oldest + timedelta(hours=1) - now).total_seconds())
            if rpd >= RPD_LIMIT:
                oldest = min(datetime.fromisoformat(e["timestamp"]) for e in events if datetime.fromisoformat(e["timestamp"]) >= one_day)
                waits.append((oldest + timedelta(hours=24) - now).total_seconds())

        sleep_for = max(0.1, max(waits) if waits else 0.1)
        log(f"Rate limit slot wait: {sleep_for:.2f}s")
        time.sleep(sleep_for)

def _check_token_limits() -> tuple[bool, str]:
    with _RATE_LOCK:
        tracker = _prune_rate_tracker(_load_rate_tracker())
        tpm, rpm, tph, rph, tpd, rpd = _rate_usage(tracker.get("events", []))
        _save_rate_tracker(tracker)

    violations = []
    if tpm >= TPM_LIMIT:
        violations.append(f"TPM {tpm}/{TPM_LIMIT}")
    if tph >= TPH_LIMIT:
        violations.append(f"TPH {tph}/{TPH_LIMIT}")
    if tpd >= TPD_LIMIT:
        violations.append(f"TPD {tpd}/{TPD_LIMIT}")
    if rpm >= RPM_LIMIT:
        violations.append(f"RPM {rpm}/{RPM_LIMIT}")
    if rph >= RPH_LIMIT:
        violations.append(f"RPH {rph}/{RPH_LIMIT}")
    if rpd >= RPD_LIMIT:
        violations.append(f"RPD {rpd}/{RPD_LIMIT}")
    if violations:
        return False, " | ".join(violations)
    return True, ""

def _run_streaming_chat(url, prompt, tab_id, session_id, history, context, mode):
    full_text = ""
    active_key = str(tab_id)
    attempt_id = ""
    state = None

    if _CEREBRAS_CLIENT is None:
        err = "Missing CEREBRAS_API_KEY environment variable."
        log(err)
        send_msg({"type": "CHAT_RESPONSE", "error": err, "tabId": tab_id, "url": url, "sessionId": session_id})
        send_msg({"type": "CHAT_STREAM_END", "error": err, "stopped": False, "tabId": tab_id, "url": url, "sessionId": session_id})
        return

    try:
        log(f"AI Stream Request for {url} (session={session_id}, model={CEREBRAS_MODEL})")

        messages = []
        if context:
            messages.append({
                "role": "system",
                "content": f"Mode: {mode}\n\nContext:\n{context}",
            })
        for h in history or []:
            role = str(h.get("role", "")).strip().lower()
            content = str(h.get("content", ""))
            if role in {"user", "assistant", "system"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": str(prompt or "")})

        while True:
            _wait_for_request_slot()
            attempt_id = str(uuid.uuid4())
            _record_request_attempt(attempt_id)
            allowed, details = _check_token_limits()
            if not allowed:
                raise Exception(f"Local rate limit hit: {details}")
            try:
                stream = _CEREBRAS_CLIENT.chat.completions.create(
                    messages=messages,
                    model=CEREBRAS_MODEL,
                    stream=True,
                    temperature=TEMPERATURE,
                    top_p=TOP_P,
                )
                break
            except Exception as stream_error:
                err = str(stream_error)
                if "429" in err or "queue_exceeded" in err or "too_many_requests_error" in err:
                    log("Queue busy. Retrying in 2.5s...")
                    time.sleep(2.5)
                    continue
                raise

        with _ACTIVE_STREAMS_LOCK:
            state = _ACTIVE_STREAMS.get(active_key)
            if state is not None:
                state["stream"] = stream

        for event in stream:
            with _ACTIVE_STREAMS_LOCK:
                state = _ACTIVE_STREAMS.get(active_key)
            if not state or state.get("stopped"):
                break

            chunk = _chunk_text_from_event(event)
            if not chunk:
                continue

            full_text += chunk
            send_msg({
                "type": "CHAT_STREAM",
                "delta": chunk,
                "tabId": tab_id,
                "url": url,
                "sessionId": session_id,
            })

        with _ACTIVE_STREAMS_LOCK:
            state = _ACTIVE_STREAMS.get(active_key) or state or {}
            was_stopped = bool(state.get("stopped"))

        if not was_stopped and not full_text.strip():
            response = _CEREBRAS_CLIENT.chat.completions.create(
                messages=messages,
                model=CEREBRAS_MODEL,
                temperature=TEMPERATURE,
                top_p=TOP_P,
            )
            full_text = _final_text_from_response(response)

        final_text = full_text.strip()
        # Estimate tokens for local limiter accounting in stream mode.
        estimated_tokens = len(str(prompt or "").split()) + len(final_text.split())
        if attempt_id:
            _finalize_request_attempt(attempt_id, estimated_tokens)

        if final_text and not was_stopped:
            memory_store.append(url, "assistant", final_text, session_id=session_id)

        send_msg({
            "type": "CHAT_STREAM_END",
            "response": final_text,
            "stopped": was_stopped,
            "tabId": tab_id,
            "url": url,
            "sessionId": session_id,
        })

    except Exception as e:
        err_text = f"Error: {e}"
        log(f"AI Stream Error: {e}")
        if attempt_id:
            _finalize_request_attempt(attempt_id, 0)
        memory_store.append(url, "assistant", err_text, session_id=session_id)
        send_msg({"type": "CHAT_RESPONSE", "error": str(e), "tabId": tab_id, "url": url, "sessionId": session_id})
        send_msg({"type": "CHAT_STREAM_END", "error": str(e), "stopped": False, "tabId": tab_id, "url": url, "sessionId": session_id})
    finally:
        with _ACTIVE_STREAMS_LOCK:
            current = _ACTIVE_STREAMS.get(active_key)
            if current and current.get("sessionId") == session_id:
                _ACTIVE_STREAMS.pop(active_key, None)

def _atomic_write_json(file_path: Path, data):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(file_path)

def save_json(data, tab_url):
    """Save the scraped data into a persistent JSON file for this URL."""
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    filename = get_safe_filename(tab_url)
    filepath = SCRAPED_DIR / filename
    _atomic_write_json(filepath, data)
    return filepath

def get_safe_filename(url: str) -> str:
    """Consistently turn a tab URL into the same JSON filename used for scraping."""
    safe_name = "".join(c if c.isalnum() else "_" for c in _normalized_url(url)).strip("_")
    return f"{safe_name[:200]}.json"

def _load_hashes() -> dict:
    if not HASHES_PATH.is_file():
        return {}
    try:
        raw = HASHES_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        try:
            corrupt = HASHES_PATH.with_suffix(HASHES_PATH.suffix + f".corrupt.{int(datetime.now(timezone.utc).timestamp())}")
            HASHES_PATH.replace(corrupt)
            log(f"Invalid hashes file moved to: {corrupt.name}")
        except Exception:
            pass
        log(f"Failed reading hashes file: {e}")
        return {}

def _save_hashes(hashes: dict):
    _atomic_write_json(HASHES_PATH, hashes)

def _build_fallback_graph(notebook_url: str):
    """Build a minimal graph from saved notebook JSON when dependency modules are unavailable."""
    json_path = SCRAPED_DIR / get_safe_filename(notebook_url)
    if not json_path.exists():
        return None
    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        cells = data.get("cells", [])
        graph = []
        for cell in cells:
            idx = cell.get("index", 0)
            code = str(cell.get("input", ""))
            graph.append({
                "cell_number": idx,
                "input_preview": code[:120],
                "dependencies": [],
                "reverse_dependencies": []
            })
        return graph
    except Exception as e:
        log(f"Fallback graph build error: {e}")
        return None

class DependencyManager:
    """Manages ContextBuilder instances for each notebook, loading from SCRAPED_DIR."""
    def __init__(self, json_dir: Path):
        self.json_dir = json_dir
        self._cache = {} # filename -> {builder, mtime}

    def get_builder(self, notebook_url: str):
        if not _DEP_AVAILABLE or not notebook_url: return None
        filename = get_safe_filename(notebook_url)
        json_path = self.json_dir / filename
        if not json_path.exists(): return None

        mtime = json_path.stat().st_mtime
        if filename not in self._cache or self._cache[filename]['mtime'] != mtime:
            log(f"Building graph for {filename}")
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cells = data.get('cells', [])
                tracker = DependencyTracker()
                cells_data = {}
                for cell in cells:
                    idx = cell.get('index', 0)
                    code = cell.get('input', '')
                    output = cell.get('output', '')
                    cells_data[idx] = {'code': code, 'output': output}
                    tracker.update_cell(idx, code)
                tracker.update_all_reverse_dependencies()
                self._cache[filename] = {
                    'builder': ContextBuilder(tracker, cells_data),
                    'mtime': mtime,
                    'cell_count': len(cells_data)
                }
            except Exception as e:
                log(f"Failed to build graph: {e}")
                return None
        return self._cache[filename]['builder']

class LocalMemoryStore:
    """Handles persistent SQLite chat history per notebook."""
    def __init__(self, db_path: Path):
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

dep_manager = DependencyManager(SCRAPED_DIR)
memory_store = LocalMemoryStore(CHAT_MEMORY_DB)

def main():
    log("=== Structured Scraper + AI Host started ===")
    while True:
        msg = read_msg()
        if msg is None:
            log("Chrome disconnected.")
            break

        m_type = msg.get("type")
        
        if m_type == "CHAT_REQUEST":
            url = _history_url_key(msg.get("url"))
            prompt = str(msg.get("prompt", ""))
            tab_id = msg.get("tabId")
            session_id = str(msg.get("sessionId") or "default")
            if not url:
                send_msg({"type": "CHAT_RESPONSE", "error": "Missing or invalid notebook URL.", "tabId": tab_id})
                continue
            
            # 1. Context
            context = ""
            builder = dep_manager.get_builder(url)
            mode = "simple" # Default
            
            if builder:
                cell_num = _extract_cell_number(prompt)
                if cell_num is not None:
                    context = builder.get_cell_context(cell_num)
                    mode = "dependency"

            # Persist lightweight profile facts (e.g., name) for stable recall.
            extracted_facts = _extract_user_profile_facts(prompt)
            for fact_key, fact_value in extracted_facts.items():
                memory_store.upsert_fact(url, fact_key, fact_value)

            facts = memory_store.get_facts(url)
            profile_context = _build_profile_memory_context(facts)

            # Deterministic fallback for critical identity recall.
            if re.search(r"\b(what\s+is|tell\s+me)\s+my\s+name\b", prompt, re.IGNORECASE):
                known_name = (facts.get("name") or "").strip()
                if known_name:
                    history = memory_store.get_history(url, session_id=session_id)
                    memory_store.append(url, "user", prompt, session_id=session_id)
                    response = f"Your name is {known_name}."
                    memory_store.append(url, "assistant", response, session_id=session_id)
                    send_msg({"type": "CHAT_RESPONSE", "response": response, "tabId": tab_id, "url": url, "sessionId": session_id})
                    continue

            if profile_context:
                if context:
                    context = f"{profile_context}\n\n{context}"
                else:
                    context = profile_context

            # Keep prompt+history dominant in normal chat; large notebook dumps hurt recall.
            if context and len(context) > MAX_CONTEXT_CHARS:
                context = context[:MAX_CONTEXT_CHARS]

            # 2. History
            history = memory_store.get_history(url, session_id=session_id)
            memory_store.append(url, "user", prompt, session_id=session_id)

            # Cancel any active stream on this tab before starting the next one.
            active_key = str(tab_id)
            with _ACTIVE_STREAMS_LOCK:
                prev = _ACTIVE_STREAMS.get(active_key)
                _stop_active_stream(prev)
            if prev and prev.get("sessionId"):
                _signal_remote_stop(prev.get("sessionId"))

            worker = threading.Thread(
                target=_run_streaming_chat,
                args=(url, prompt, tab_id, session_id, history, context, mode),
                daemon=True,
            )
            with _ACTIVE_STREAMS_LOCK:
                _ACTIVE_STREAMS[active_key] = {
                    "thread": worker,
                    "sessionId": session_id,
                    "stopped": False,
                    "url": url,
                }
            worker.start()
            continue

        if m_type == "STOP_CHAT":
            tab_id = msg.get("tabId")
            active_key = str(tab_id)
            session_id = str(msg.get("sessionId") or "")
            with _ACTIVE_STREAMS_LOCK:
                state = _ACTIVE_STREAMS.get(active_key)
                if state:
                    _stop_active_stream(state)
                    if not session_id:
                        session_id = str(state.get("sessionId") or "")
            _signal_remote_stop(session_id)
            send_msg({"type": "CHAT_STREAM_END", "stopped": True, "tabId": tab_id, "url": _history_url_key(msg.get("url")), "sessionId": session_id})
            continue

        def push_graph(url, tid):
            if not tid:
                return
            try:
                b = dep_manager.get_builder(url)
                if b:
                    t = b.tracker
                    gd = []
                    for num, data in b.cells.items():
                        gd.append({
                            'cell_number': num,
                            'input_preview': data.get('code', '')[:120],
                            'dependencies': t.get_dependencies(num, transitive=False),
                            'reverse_dependencies': t.get_reverse_dependencies(num)
                        })
                    send_msg({"type": "GRAPH_DATA", "graph": gd, "tabId": tid, "url": url})
                    return

                fallback_graph = _build_fallback_graph(url)
                if fallback_graph is not None:
                    note = None
                    send_msg({"type": "GRAPH_DATA", "graph": fallback_graph, "tabId": tid, "error": note, "url": url})
                    return

                send_msg({"type": "GRAPH_DATA", "graph": [], "tabId": tid, "error": "No notebook data available yet for this page.", "url": url})
            except Exception as e:
                log(f"Push Graph Error: {e}")
                send_msg({"type": "GRAPH_DATA", "graph": [], "tabId": tid, "error": f"Graph generation failed: {e}", "url": url})

        if m_type == "GET_GRAPH":
            url = _normalized_url(msg.get("url") or "")
            tab_id = msg.get("tabId")
            push_graph(url, tab_id)
            continue

        if m_type == "GET_HISTORY":
            url = _history_url_key(msg.get("url"))
            tab_id = msg.get("tabId")
            session_id = str(msg.get("sessionId") or "default")
            history = memory_store.get_history(url, session_id=session_id) if url else []
            sessions = memory_store.list_sessions(url) if url else []
            send_msg({
                "type": "HISTORY_DATA",
                "history": history,
                "sessions": sessions,
                "activeSessionId": session_id,
                "tabId": tab_id,
                "url": url,
            })
            continue

        if m_type == "CLEAR_HISTORY":
            url = _history_url_key(msg.get("url"))
            tab_id = msg.get("tabId")
            session_id = msg.get("sessionId")
            if url:
                memory_store.clear_history(url, session_id=session_id)
            send_msg({"type": "HISTORY_CLEARED", "url": url, "tabId": tab_id, "sessionId": session_id})
            continue

        if m_type == "NOTEBOOK_DATA":
            tab_url = _normalized_url(msg.get("tabUrl") or "unknown")
            tab_id = msg.get("tabId")
            raw_cells = msg.get("cells", [])
            if not isinstance(raw_cells, list):
                raw_cells = []
            
            code_cells = []
            for i, cell in enumerate(raw_cells):
                if cell.get("type") == "code":
                    code_cells.append({
                        "index": i + 1,
                        "input": str(cell.get("source") or ""),
                        "output": str(cell.get("output") or "")
                    })
            
            import hashlib
            data_str = json.dumps(code_cells, sort_keys=True).encode("utf-8")
            data_hash = hashlib.sha256(data_str).hexdigest()

            with _HASHES_LOCK:
                stored_hashes = _load_hashes()

                prev_hash = stored_hashes.get(tab_url)
                if prev_hash != data_hash:
                    final_data = {
                        "tabUrl": tab_url,
                        "title": str(msg.get("title", "notebook")),
                        "lastUpdated": datetime.now(timezone.utc).isoformat(),
                        "cells": code_cells
                    }
                    save_json(final_data, tab_url)

                    stored_hashes[tab_url] = data_hash
                    _save_hashes(stored_hashes)

                    push_graph(tab_url, tab_id)
        
        send_msg({"ok": True})

def initialize():
    """Prepare all subdirectories in the data folder."""
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    CHAT_MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RATE_LIMIT_TRACKER.parent.mkdir(parents=True, exist_ok=True)
    if _DEP_FALLBACK:
        log("Dependency modules not found; using built-in fallback dependency engine.")
    elif not _DEP_AVAILABLE:
        log("Dependency mode modules not found; dependency graph features disabled.")
    log("=== Local Data Registry Organized ===")

def entry_point():
    """Run the scraper and handle graceful shutdown on interrupt or error."""
    try:
        initialize()
        main()
    except KeyboardInterrupt:
        log("Interrupted by user – shutting down.")
    except Exception as e:
        log(f"Unexpected error: {e}")
        raise

if __name__ == "__main__":
    entry_point()
