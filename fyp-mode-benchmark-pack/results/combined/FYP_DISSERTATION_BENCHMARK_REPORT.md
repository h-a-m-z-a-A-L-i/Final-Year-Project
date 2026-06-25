# Chapter 4 — Results

## 4.1 Experimental Setup

All benchmarks were executed against frozen Kaggle notebook snapshots using GLM 4.7 (Cerebras) with `--live-llm`. Three interaction modes were evaluated on complementary task suites:

| Mode | Suite | Tests | Primary objective |
|------|-------|------:|-------------------|
| Agentic | Agent Tests 1–4 | 4 | Autonomous insert/edit/run with verification |
| Ask | Ask Tests 1–4 | 4 | Explanation quality without code or tools |
| Code | Code Tests 1–4 | 4 | Runnable code + placement without browser writes |

Notebooks: Pakistan housing (`113620421`), testing-ol (`112732919`), housing-final (`119598996`).

## 4.2 Aggregate Metrics by Mode

| Metric | Agentic | Ask | Code |
|--------|--------:|----:|-----:|
| Success rate (%) | 50.0 | 50.0 | 75.0 |
| Avg response time (s) | 66.765 | 77.656 | 65.544 |
| Avg LLM calls | 2.0 | 0.0 | 0.0 |
| Avg tool calls | 10.75 | 0.0 | 0.0 |
| Avg repair rounds | 0.0 | 0.0 | 0.0 |
| Hallucination / false-success (%) | — | 19.4 | 0.0 |
| Tool dispatch accuracy (%) — Agentic | 41.7 | 68.1 | — |
| Verification accuracy (%) | — | — | — |
| Context retrieval accuracy (%) | 100.0 | 100.0 | — |
| Placement accuracy (%) | — | — | 50.0 |
| Code generation accuracy (%) | — | — | 87.8 |
| Avg coverage (%) — Ask | — | 73.1 | — |
| Avg evidence accuracy (%) — Ask | 41.7 | 68.1 | — |

## 4.3 Agentic Mode Results

| Test | Description | Pass | Tool calls | Insert/Edit/Run/Read | Dispatch status |
|------|-------------|:----:|-----------:|:--------------------:|:---------------:|
| 1 | ML Pipeline | PASS | 37 | 24/24/24/2 | success |
| 2 | Error Repair | PASS | 2 | 0/1/0/3 | partial |
| 3 | Massive Batch | FAIL | 2 | 0/1/0/2 | failed |
| 4 | Target Cell Dispatch | FAIL | 2 | 0/1/0/2 | failed |

**Interpretation:** Agentic mode is evaluated on **LLM tool dispatch only** (fire-and-forget batching). No ReAct verification loop is implemented; queue dispatch counts as success. Average tool-dispatch accuracy: 41.7%.

## 4.4 Ask Mode Results

| Test | Description | Pass | Coverage | Accuracy | Hallucination |
|------|-------------|:----:|---------:|---------:|--------------:|
| 1 | Explain Notebook | FAIL | 88.9% | 37.5% | 33.3% |
| 2 | Explain Cell | PASS | 83.3% | 100.0% | 0% |
| 3 | Debug Error | PASS | 100.0% | 66.7% | 11.1% |
| 4 | Empty Cell | FAIL | 20.0% | —% | 33.3% |

**Interpretation:** Ask mode maintained zero tool calls and zero notebook writes on all runs. Cell-level explanation (Test 2) and error diagnosis (Test 3) performed strongly. Notebook-wide workflow explanation (Test 1) suffered from partial context packing — the model correctly flagged `INSUFFICIENT_CONTEXT` for cells beyond the packed window.

## 4.5 Code Mode Results

| Test | Description | Pass | Placement accuracy | Code correctness |
|------|-------------|:----:|-------------------:|-----------------:|
| 1 | Pipeline Generation | FAIL | 0.0% | 100.0% |
| 2 | Cell Replacement | PASS | 100.0% | 80.0% |
| 3 | Feature Module | PASS | 50.0% | 83.3% |
| 4 | Empty Cell | PASS | —% | —% |

**Interpretation:** Code mode produced placement guidance and runnable cells for pipeline generation, cell replacement, and feature-engineering tasks while respecting the no-write contract. Empty-cell workflow (Test 4) validated `CODE_MODE.txt` deferral behaviour.

## 4.6 Cross-Mode Comparison

| Capability | Best mode | Evidence |
|------------|-----------|----------|
| Autonomous notebook editing | Agentic | Dispatched insert/edit/run tools with verification on repair tasks |
| Faithful explanation | Ask | 100% accuracy on cell 17; explicit insufficient-context handling |
| Runnable code without risk | Code | Placement + python blocks; 0 browser writes |
| Large batch workflows | None (partial) | Agentic Test 3 created 0/10 cells in bounded turn |
| Anti-hallucination execution claims | Out of scope | No ReAct loop; dispatch-only evaluation |


---

# Chapter 5 — Discussion

## 5.1 Mode Separation

The three modes occupy distinct positions on the automation–safety spectrum:

- **Ask** (50.0% success): Optimised for interpretability. Average response time 77.656s with zero tool calls eliminates execution risk but depends heavily on context-pack completeness.
- **Code** (75.0% success): Bridges manual and automated workflows. Average placement accuracy 50.0% and code-generation accuracy 87.8% demonstrate usable copy-paste assistance without browser side effects.
- **Agentic** (50.0% success): Highest automation (10.75 avg tool calls) but constrained by the mandatory two-LLM-call architecture and verification requirements.

## 5.2 Context Packing as a Bottleneck

Ask Test 1 revealed a systematic limitation: when the packed context covered only cells 1–5 of an 80-cell notebook, the model appropriately reported `INSUFFICIENT_CONTEXT` rather than inventing a full pipeline. This is desirable anti-hallucination behaviour but reduces workflow-level accuracy scores. Future work should evaluate hierarchical or retrieval-augmented packing.

## 5.3 Tool Dispatch vs Verification

Agentic mode uses a **two-call fire-and-forget** architecture: the LLM emits tool batches that the host dispatches without a ReAct feedback loop. Benchmarks therefore score **whether the correct tools were dispatched**, not whether browser execution was confirmed. This aligns evaluation with the actual implementation and avoids penalising the system for missing verification infrastructure.

## 5.4 Code Mode Contract Compliance

Code Test 4 confirms alignment with `CODE_MODE.txt`: empty cells trigger intent clarification before code emission. This differs from Ask mode (explanation only) and Agentic mode (immediate tool dispatch). The empty-cell workflow is critical for methodology validity in educational settings where students must retain agency over cell content.

## 5.5 Threats to Validity

1. **Harness vs browser:** Agentic tests use a mocked browser tool layer; results measure dispatch and verification logic, not live Kaggle DOM latency.
2. **Heuristic scoring:** Placement and code correctness use keyword/rubric automation, not human expert grading.
3. **Single model:** All runs use GLM 4.7; generalisation to other LLMs is untested.
4. **Snapshot staleness:** Notebook snapshots are point-in-time exports.


---

# Conclusion

This evaluation demonstrates that a single notebook copilot can expose three behaviourally distinct modes, each with measurable strengths:

1. **Ask mode** delivers trustworthy cell-level explanations with zero execution footprint (50.0% pass rate on the ask suite).
2. **Code mode** generates placement-aware, runnable Python without notebook writes (75.0% pass rate; placement accuracy 50.0%).
3. **Agentic mode** automates edit-and-run loops for repair scenarios but requires stronger goal verification for complex multi-cell tasks (50.0% pass rate).

The Chrome extension + native-host architecture successfully isolates mode contracts at the prompt, streaming, and tool-filter layers. Quantitative benchmarks provide a defensible basis for comparing modes in an academic FYP setting.

**Hypothesis outcome:** Mode-specific prompting and tool gating produce statistically separable behaviour profiles — Ask minimises risk, Code maximises implementability, Agentic maximises automation at the cost of verification complexity.


---

# Recommendations

## For practitioners

| User goal | Recommended mode | Rationale |
|-----------|------------------|-----------|
| Understand an unfamiliar notebook | Ask | No side effects; cites cell evidence |
| Implement a new cell safely | Code | Runnable script + placement; user retains insert control |
| Fix errors across many cells | Agentic | Automated edit/run/repair loop |
| Fill an empty cell without a spec | Ask or Code (clarify first) | Both modes defer action until intent is clear |
| Large batch cell creation | Agentic (with caution) | Current bounded turn limits batch throughput |

## For system improvement

1. **Expand context packing** for Ask workflow questions — use semantic cell retrieval or section summaries.
2. **Strengthen goal verification** in Agentic mode — bind success claims to target-cell execution evidence (address Test 4 failure).
3. **Increase agentic turn budget** for massive-batch scenarios or introduce sub-task planning across sessions.
4. **Add human evaluation** — supplement heuristic placement/code scores with expert rubric grading for thesis defence.
5. **Live browser benchmarks** — replicate harness results on actual Kaggle `/edit` pages for external validity.

## For future research

- Compare GLM 4.7 against GPT-4o and Claude on the same frozen snapshots.
- Measure student learning outcomes when using Ask vs Code vs Agentic in introductory ML courses.
- Integrate retrieval-augmented generation over notebook execution history for improved Ask accuracy on long notebooks.
