# Chapter 3 Evaluation — Agentic Mode (Dispatch-Only)

**Evaluation:** LLM tool dispatch only (fire-and-forget). **No ReAct verification loop.**

**Model:** GLM 4.7 (Cerebras) | **Run:** 2026-06-16

## Summary

| Test | Pass | Insert/Edit/Run/Read | Status |
|------|:----:|:--------------------:|--------|
| 1 ML Pipeline | **PASS** | 24/24/24/2 | success |
| 2 Error Repair | **PASS** | 0/1/0/3 | partial |
| 3 Massive Batch | FAIL | 0/1/0/2 | failed |
| 4 Target Cell (31) | FAIL | 0/1/0/2 | failed |

**Overall:** 2/4 pass (50%).

## Pass criteria

| Test | Pass when |
|------|-----------|
| 1 | Writes + runs + reads dispatched |
| 2 | In-place `edit_cell` dispatched |
| 3 | ≥10 `insert_cell` in LLM batch |
| 4 | `edit_cell` + `run_cell` for cell 31 |

## Reproduce

```powershell
python testing/host/scripts/run_agent_tests.py --live-llm
```
