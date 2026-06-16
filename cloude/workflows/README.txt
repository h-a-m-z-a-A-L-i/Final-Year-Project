================================================================================
NORMAL-CHROME WORKFLOW DOCUMENTATION — INDEX
================================================================================
Repository: normal-chrome (FYP — Kaggle Notebook Copilot)
Location: cloude/workflows/
Companion: ../PROJECT_DESCRIPTION.txt (full project overview)

================================================================================
PROJECT SUMMARY (ONE PARAGRAPH)
================================================================================

Normal-chrome is a Chrome extension paired with a Python native-messaging host
that adds an AI copilot to Kaggle notebook editor pages. The extension scrapes
live Jupyter cells from the browser DOM, forwards structured snapshots to the
host, and renders a sidebar chat UI (plus per-cell debug panels). The host
stores notebook JSON snapshots (live + verified persistent), assembles LLM
prompts with notebook evidence, streams responses from Cerebras (gpt-oss-120b),
and persists chat history in SQLite keyed by stable Kaggle kernel IDs. Users
interact in Ask mode (explain/debug), Code mode (generate runnable cells with
placement guidance), or Agentic mode (two-phase tool execution: first LLM call
for reads, second for browser write/run tools). The primary path grounds
answers in scraped notebook context embedded in the system prompt;
supplementary subsystems include dependency-graph context slicing, host-side
read-tool prefetch, and agentic_batch_executor for tool batch dispatch.

================================================================================
WORKFLOW FILES (5)
================================================================================

1. SYSTEM_ARCHITECTURE_AND_MESSAGE_BRIDGE.txt
   Chrome extension ↔ background.js ↔ native messaging ↔ host.py;
   Kaggle page components; message types; storage overview.

2. NOTEBOOK_SCRAPE_AND_DATA_PIPELINE.txt
   DOM scrape → NOTEBOOK_DATA → notebook_data_handler; identity resolution;
   live vs persistent snapshots; pack_context entry point; URL migration.

3. CHAT_REQUEST_TO_LLM_RESPONSE.txt
   Full chat orchestration path shared by all modes: UI send through streaming
   loop, history, prompt assembly, context budget, CHAT_STREAM back to UI.

4. ASK_MODE_AND_CODE_MODE_WORKFLOWS.txt
   Side-by-side Ask vs Code end-to-end workflows; prompt files; context slicing;
   prefetch tools; output format differences.

5. AGENTIC_MODE_TOOL_EXECUTION.txt
   Agentic gates; two-phase agentic flow (first + second LLM call);
   agentic_batch_executor queue; browser vs local tools; dispatch summary;
   optional verification follow-up when fire-and-forget is disabled.

================================================================================
SUGGESTED READING ORDER
================================================================================

For a new developer or LLM context load:

  Step 1 — SYSTEM_ARCHITECTURE_AND_MESSAGE_BRIDGE.txt
           Understand the three-tier layout and how JSON messages flow.

  Step 2 — NOTEBOOK_SCRAPE_AND_DATA_PIPELINE.txt
           Understand how notebook evidence reaches the host before any chat.

  Step 3 — CHAT_REQUEST_TO_LLM_RESPONSE.txt
           Understand the shared chat pipeline (applies to all modes).

  Step 4 — ASK_MODE_AND_CODE_MODE_WORKFLOWS.txt
           Understand the two primary user-facing modes (read-only vs generate).

  Step 5 — AGENTIC_MODE_TOOL_EXECUTION.txt
           Understand browser automation and the two-phase agentic flow.

Cross-references: each file names sibling files where paths diverge or rejoin.

================================================================================
SOURCE CODE ROOTS
================================================================================

  Extension:  testing/extension/
  Host:       testing/host/
  Data:       testing/host/data/
  Prompts:    testing/host/prompts/
  Mode docs:  testing/host/docs/ASK_MODE.txt, CODE_MODE.txt,
              AGENTIC_ENGINE_REFERENCE.txt

================================================================================
END OF README
================================================================================
