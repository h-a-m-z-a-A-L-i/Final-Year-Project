# Tool Refusal Study Report

**Generated:** 2026-06-14T18:22:07.050412+00:00
**Model:** gpt-oss-120b (cerebras)
**Notebook:** `https://www.kaggle.com/code/codekey/testing-ol/edit`

## Phase 3 — Frequency (50 action-required prompts)

| Metric | Count |
|--------|------:|
| total_prompts | 50 |
| tool_batches (parsed_tool_count > 0) | 49 |
| tool_refusals (parsed_tool_count == 0) | 1 |
| parse_failures (MALFORMED/EMPTY/UNKNOWN) | 0 |
| prose_only | 1 |
| tool_refusal (explicit) | 0 |

**Tool batch rate:** 98.0%
**Refusal rate:** 2.0%

## Phase 2 — Failure type breakdown

```json
{
  "PROSE_ONLY": 1
}
```

## Phase 4 — Prompt size vs refusals

- **prompt_est_tokens** (refusals): {"refusal_count": 1, "mean": 5935.0, "median": 5935.0, "max": 5935.0}
- **state_block_chars** (refusals): {"refusal_count": 1, "mean": 184.0, "median": 184.0, "max": 184.0}
- **semantic_index_chars** (refusals): {"refusal_count": 1, "mean": 0.0, "median": 0.0, "max": 0.0}
- **dependency_graph_chars** (refusals): {"refusal_count": 1, "mean": 0.0, "median": 0.0, "max": 0.0}
- **runtime_state_chars** (refusals): {"refusal_count": 1, "mean": 0.0, "median": 0.0, "max": 0.0}

## Phase 5 — Recovery experiment (failed prompts only)

| Strategy | Recovery message | Attempts | Successes | Rate |
|----------|------------------|----------|-----------|------|
| A | Retry the request.… | 1 | 1 | 100.0% |
| B | You MUST respond with exactly one <agent_tool_batc… | 1 | 1 | 100.0% |
| C | No prose. Tool calls only.… | 1 | 1 | 100.0% |

**Best recovery strategy:** A (1/1)

## Phase 6 — Answers

1. **Did GPT-OSS refuse tool mode?** Yes — 0 explicit refusals; 1 total zero-tool responses.
2. **How often?** 2.0% of prompts (1/50) returned zero parsed tools.
3. **Correlated with prompt size?** See size_stats above. Compare mean prompt_est_tokens: refusals=5935.0 vs successes=5924.8.
4. **Best recovery?** Strategy **A**.
5. **Model vs host?** Primarily **model behavior** (prose/instruction-style replies without `<agent_tool_batch>`). Host guard correctly blocks false success; logging in `agent_tool_refusal.jsonl` captures raw responses.

## Production failure: "manual instructions instead of tool calls"

This message is emitted by the **host action guard** (`streaming.py`) when **all** of:

- `agentic_tools_executed == 0` and no batch ran in the turn
- `is_actionable_notebook_request(prompt)` is true
- `looks_like_instruction_only_response(final_text)` is true (≥2 instruction markers like "Placement", "insert below", "run order")

This is **distinct** from a single-round refusal logged above. In the live Cell 30 session, the model may emit tools in some rounds but the **final** stored response is prose with placement/run-order language and **zero tools executed in that turn**.

**Host evidence (prior session):** chat history shows LLM 400 errors:

```text
messages.*._react_agent_state: property '..._react_agent_state' is unsupported
```

That API rejection can prevent tool rounds from completing and leave the turn with prose-only output that triggers the guard. The study script strips internal keys via `messages_for_api()` before calling Cerebras; verify the live host path always does the same (it does for LLM calls; logging retains full messages for inspection).

**Cell 30 prompt in isolation (study t01):** GPT-OSS **did** emit tools (`notebook_get_cell` batch) when called with a clean API payload — so the issue is **not** universal tool-mode refusal for that prompt.

## Diagnostics added (Phases 1–2)

- **`agent_tool_refusal.jsonl`** — appended when `action_required && parsed_tool_count == 0` during ReAct, and when the final instruction-only guard fires.
- **`classify_tool_refusal_failure()`** — categories: `TOOL_REFUSAL`, `PROSE_ONLY`, `MALFORMED_BATCH`, `EMPTY_BATCH`, `UNKNOWN_TOOL_ONLY`.
- **`prompt_inspection`** block on each record: message counts, est. tokens, state/planner/semantic/dependency/runtime char sizes.

Re-run study: `python testing/host/scripts/tool_refusal_study.py`


## Sample refusals (first 3)

### p30
- failure_type: `PROSE_ONLY`
- prompt_est_tokens: 5935
- preview: The markdown cell will be inserted right after cell 30 (by creating a markdown cell before the current cell 31) and then filled with the explanation.…
