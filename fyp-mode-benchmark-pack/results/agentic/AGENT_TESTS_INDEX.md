# Agent Evaluation Suite — Index

**Evaluation:** LLM tool dispatch only (fire-and-forget). No ReAct verification loop.

```powershell
python testing/host/scripts/run_agent_tests.py --live-llm
```

| # | Test | Kernel | Pass | Insert/Edit/Run/Read | Status |
|---|------|--------|:----:|:--------------------:|--------|
| 1 | Complete ML Pipeline | 113620421 | PASS | 24/24/24/2 | success |
| 2 | Error Repair | 112732919 | PASS | 0/1/0/3 | partial |
| 3 | Massive Multi-Tool Batch | 119598996 | FAIL | 0/1/0/2 | failed |
| 4 | Target Cell Dispatch (cell 31) | 112732919 | FAIL | 0/1/0/2 | failed |

Per-test: `AGENT_TEST_*_summary.md` / `*_report.md`
