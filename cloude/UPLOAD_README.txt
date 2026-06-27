================================================================================
CLOUDE UPLOAD PACKAGE — normal-chrome (Kaggle Notebook Copilot)
================================================================================
Path: cloude/  (curated snapshot for Claude AI / project upload)
Generated: June 2026

WHAT THIS PACKAGE IS
--------------------
A workflow-focused copy of the normal-chrome Final Year Project: Chrome
extension + Python native messaging host that adds an AI copilot to Kaggle
notebook editor pages. It includes source, prompts, API modules, tests, scripts,
and technical mode references — without runtime data, secrets, or large
artifacts.

ACTIVE STACK (start here)
-------------------------
  testing/extension/     Manifest + content scripts + copilot UI (ui_injector.js)
  testing/host/host.py   Native messaging entry; routes CHAT_REQUEST by mode
  native/                Chrome native host manifest + python_host.py launcher

Legacy root extension/ and step1_backend.py are NOT included (superseded).

THREE CHAT MODES (UI dropdown is authoritative)
-----------------------------------------------
  ask      — Read-only advisory: explain cells, debug errors, placement prose.
             No full copy-paste cell scripts; no browser writes.
  code     — Generates runnable Python + placement/run-order in chat.
             User copies code; host does not auto-edit the notebook.
  agentic  — Two-phase agentic flow: first LLM call (read tools), second LLM
             call (implementation tool_calls for insert/edit/run cells).
             See testing/host/docs/AGENTIC_ENGINE_REFERENCE.txt.

Key references:
  testing/host/docs/ASK_MODE.txt
  testing/host/docs/CODE_MODE.txt
  testing/host/docs/AGENTIC_ENGINE_REFERENCE.txt
  testing/host/prompts/          (ask.txt, code.txt, agentic, etc.)
  PROJECT_DESCRIPTION.txt        (full system overview)

SUGGESTED READING ORDER (for end-to-end understanding)
------------------------------------------------------
  0. workflows/README.txt  → 5 end-to-end workflow docs (start here)
  1. PROJECT_DESCRIPTION.txt
  2. testing/host/docs/ASK_MODE.txt, CODE_MODE.txt (user-facing contracts)
  3. testing/host/host.py (message routing) + testing/host/streaming.py (chat)
  4. testing/host/prompt_engineering.py + testing/host/prompts/*
  5. testing/extension/ui_injector.js + background.js (UI → host bridge)
  6. testing/host/docs/AGENTIC_ENGINE_REFERENCE.txt (if working on agentic)
  7. testing/host/tool_registry.py + *\_tool.py + verification_suite.py
  8. testing/host/tools_testing/ (per-tool harnesses + verify_* tools)
  9. testing/host/tests/ (pytest suite) and scripts/ (FYP benchmarks)

MESSAGE TYPES CHEAT SHEET (extension ↔ host)
--------------------------------------------
UI / background → host (native messaging):
  CHAT_REQUEST          User message; fields: mode (ask|code|agentic), prompt, url, tabId, ...
  STOP_CHAT             Cancel in-flight stream
  GET_GRAPH             Dependency graph for notebook url
  GET_HISTORY           SQLite chat history for notebook/session
  CLEAR_HISTORY         Clear history for notebook key
  NOTEBOOK_DATA         Scraped cells / iframe payload from extension
  NOTEBOOK_URL_CHANGED  Tab navigated to new notebook URL
  PROMPT_SIGNAL         Prompt-observer telemetry from page
  RESOLVE_NOTEBOOK_IDENTITY   Map URL → stable Kaggle kernel id
  GET_AGENTIC_SETTINGS / SET_AGENTIC_SETTINGS
  INSERT_CODE_CELL      Host-driven insert (not auto from Code chat)

Host → extension / UI:
  CHAT_STREAM           Streaming token delta (markdown)
  CHAT_STREAM_END       Stream finished (error, stopped, notebookKey, sessionId)
  (Graph/history responses routed via background.js handlers)

Content scripts (examples):
  KERNEL_STATE_UPDATE   Kernel busy/idle from kernel_state_listener.js
  PING / PONG           Health check

Configure API keys via .env.example → local .env (NOT included).

EXCLUDED FROM THIS PACKAGE (and why)
------------------------------------
  testing/host/data/**     Logs, notebook JSON snapshots, sessions, sqlite DBs
  .env                     Secrets / API keys
  .venv, __pycache__, .pytest_cache, *.pyc
  latex format/, research papers/, database/, kaggle json dumps at repo root
  Root extension/, step1_backend.py   Legacy paths
  testing/extension/markdown-it.min.js  Optional vendored dep (large minified)
  testing/extension/ui_injector_gen.js  Generated duplicate of ui_injector.js
  native/*.generated.*     Machine-generated host registration artifacts

OPTIONAL DEV FILES INCLUDED
---------------------------
  testing/host/smoke_test_*.py at host root (manual dev smoke tests)
  testing/host/tests/ full pytest suite
  testing/host/scripts/ FYP experiment + benchmark JSON configs

SETUP HINT (not required for code review)
-----------------------------------------
  pip install -r requirements.txt
  Register native host (native/register_host.ps1) and load unpacked extension
  from testing/extension/ in Chrome.

================================================================================
