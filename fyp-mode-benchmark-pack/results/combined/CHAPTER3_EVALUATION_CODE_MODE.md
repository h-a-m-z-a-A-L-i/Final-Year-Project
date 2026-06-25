# Chapter 3 Evaluation — Code Mode Benchmark Suite (v2)

**Suite:** CODE_MODE_BENCHMARK_SUITE  
**Model:** GLM 4.7 (Cerebras) | **Run:** 2026-06-16 | **Live LLM:** yes

## Summary

| Test | Name | Pass | Placement accuracy | Code correctness |
|------|------|:----:|-------------------:|-----------------:|
| 1 | Pipeline Generation (XGBoost) | FAIL | 0% | 100% |
| 2 | Cell Replacement (cell 21) | PASS | 100% | 80% |
| 3 | New Feature Module | PASS | 50% | 83.3% |
| 4 | Empty Cell Workflow (cell 50) | PASS | — | — |

**Overall:** 3/4 passed. Zero browser tool calls and zero notebook writes on all runs.

## Key findings

- **Test 1:** Correct XGBoost code using `X_train`/`y_train`, but placement cited early cells outside the model-training region (context-pack limitation) → placement accuracy 0%.
- **Test 2:** Robust preprocessing replacement for cell 21; preserves `log_price` / `df1` downstream compatibility.
- **Test 3:** Interaction + polynomial + missing-value handling in one cell; partial placement score (50%).
- **Test 4:** `CODE_MODE.txt` compliance — acknowledged empty cell 50, asked clarifying questions, deferred code generation.

## Reproduce

```powershell
python testing/host/scripts/run_code_tests.py --live-llm
python testing/host/scripts/generate_fyp_dissertation_report.py
```

**Full dissertation report:** `FYP_DISSERTATION_BENCHMARK_REPORT.md`
