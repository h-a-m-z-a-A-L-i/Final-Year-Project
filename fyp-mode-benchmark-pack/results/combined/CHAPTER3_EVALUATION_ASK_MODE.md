# Chapter 3 Evaluation — Ask Mode Benchmark Suite

**Suite:** ASK_MODE_BENCHMARK_SUITE  
**Model:** GLM 4.7 (Cerebras)  
**Run date:** 2026-06-16  
**Live LLM:** yes  
**Purpose:** Measure explanation quality only — no code generation, no tool execution, no notebook modifications.

## Summary

| Test | Name | Kernel | Pass | Coverage | Accuracy | Hallucination (heuristic) |
|------|------|--------|:----:|---------:|---------:|--------------------------:|
| 1 | Explain Notebook | 113620421 | FAIL | 88.9% | 37.5% | 33.3% |
| 2 | Explain Cell | 113620421 | PASS | 83.3% | 100% | 0% |
| 3 | Debug Error | 112732919 | PASS | 100% | 66.7% | 11.1% |
| 4 | Empty Cell Handling | 113620421 | FAIL | 20.0% | — | 33.3% |

**Overall:** 2/4 passed. Contract compliance (no tools, no writes, no large code dumps) held on all four runs.

## Test 1 — Explain Notebook

**Prompt:** Explain complete workflow (data loading, cleaning, feature engineering, model training, evaluation). No code. Notebook evidence only.

**Metrics:**
- Coverage: 88.9% (8/9 topics; missing: visualizations)
- Accuracy (notebook-term alignment): 37.5%
- Tool calls: 0 | Execution time: ~62s
- **Result:** FAIL — strong topical coverage but weak alignment with notebook-specific evidence terms (Pakistan housing, LightGBM, etc.)

**Artifact:** `ASK_TEST_1_EXPLAIN_NOTEBOOK_kaggle_kernel_113620421_summary.md`

## Test 2 — Explain Cell 17

**Notebook:** Pakistan housing (`113620421`). Cell 17 drops `date_added`.

**Metrics:**
- Coverage: 83.3% | Accuracy: 100% (ground truth: `date_added`, `drop`)
- Hallucination rate: 0%
- **Result:** PASS — correct inputs/outputs/deps; no replacement code generated.

**Artifact:** `ASK_TEST_2_EXPLAIN_CELL_kaggle_kernel_113620421_summary.md`

## Test 3 — Debug Error (Cell 31)

**Notebook:** testing-ol (`112732919`). Cell 31: `NameError: name 'df' is not defined`.

**Metrics:**
- Coverage: 100% (root cause, fix, impact, minimal)
- Accuracy: 66.7% (2/3 ground-truth terms)
- **Result:** PASS — root cause identified; minimal fix suggested; no full replacement script.

**Artifact:** `ASK_TEST_3_DEBUG_ERROR_kaggle_kernel_112732919_summary.md`

## Test 4 — Empty Cell Handling (Cell 45)

**Fixture:** `fyp_ask_empty_cell_113620421.json` (cell 45 cleared for test).

**Contract checklist:**
- Acknowledges empty cell: **yes**
- Asks clarifying questions: **yes**
- Avoids full code solution: **yes**

**Metrics:**
- Coverage: 20% (keyword rubric missed phrasing variants)
- **Result:** FAIL on automated rubric; **contract compliance PASS**

**Artifact:** `ASK_TEST_4_EMPTY_CELL_kaggle_kernel_113620421_summary.md`

## Methodology notes (for LaTeX)

- Automated **coverage** = fraction of required topic keywords present in response.
- **Accuracy** = alignment with ground-truth terms from snapshot (cell source or notebook domain).
- **Hallucination rate** = heuristic combining invalid cell references, fabrication flags, and missed ground truth.
- **Contract compliance** = zero tool calls, zero notebook writes, no large code blocks (Ask mode).

## Reproduce

```powershell
python testing/host/scripts/run_ask_tests.py --live-llm
```

Full index: `testing/host/data/logs/ASK_MODE_BENCHMARK_SUITE_INDEX.md`
