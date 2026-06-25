# FYP Mode Benchmark Pack (Ask / Code / Agentic)

This folder collects everything needed to **re-run** or **demonstrate** the three interaction-mode benchmark suites used in the dissertation (Chapter 3 evaluation + Chapter 4 results).

> **Important:** The runners call the full Python host under `testing/host/` (LLM via Cerebras, in-process harness with mocked browser tools). Run commands from the **repository root** (`d:\FYP\normal-chrome`), not from inside this folder.

---

## Folder layout

```
fyp-mode-benchmark-pack/
├── README.md                 ← this file
├── run_all_benchmarks.ps1    ← run Ask + Code + Agentic (live LLM)
├── restore_notebooks.ps1     ← copy frozen snapshots into testing/host
├── scripts/                  ← copies of benchmark runners + JSON configs
├── notebooks/                ← frozen Kaggle notebook JSON snapshots
└── results/
    ├── ask/                  ← saved Ask-mode outputs (thesis run)
    ├── code/                 ← saved Code-mode outputs
    ├── agentic/              ← saved Agentic-mode outputs
    └── combined/             ← aggregate dissertation report
```

---

## Prerequisites

1. **Python 3.10+** with project dependencies installed (same env used for `testing/host/host.py`).
2. **Cerebras API key** in `testing/host/.env`:
   ```env
   LLM_PROVIDER=cerebras
   CEREBRAS_API_KEY=your_key_here
   CEREBRAS_MODEL=zai-glm-4.7
   LLM_AGENTIC_ENABLED=1
   ```
   Copy from `.env.example` at repo root if needed.
3. **Frozen notebook snapshots** in `testing/host/data/notebooks/persistent/` (run `restore_notebooks.ps1` first if missing).

---

## Quick start (re-run all three modes)

From **PowerShell**, repository root:

```powershell
cd d:\FYP\normal-chrome

# 1. Restore notebook snapshots from this pack
.\fyp-mode-benchmark-pack\restore_notebooks.ps1

# 2. Run all suites with live LLM (requires API key; takes several minutes)
.\fyp-mode-benchmark-pack\run_all_benchmarks.ps1
```

New results are written to `testing/host/data/logs/` (same filenames as in `results/` here).

---

## Commands (run from repository root)

### Restore notebook snapshots only

```powershell
.\fyp-mode-benchmark-pack\restore_notebooks.ps1
```

### Ask mode (Tests 1–4)

Explanation quality; **no tools**, **no notebook writes**.

```powershell
python testing/host/scripts/run_ask_tests.py --live-llm
```

Single test:

```powershell
python testing/host/scripts/run_ask_tests.py --live-llm --test 2
```

Rebuild markdown from existing JSON (no new LLM calls):

```powershell
python testing/host/scripts/run_ask_tests.py --regenerate
```

**Config:** `scripts/fyp_experiment_benchmarks_ask.json`  
**Outputs:** `testing/host/data/logs/ASK_TEST_*_{results,summary,report}.{json,md}`  
**Suite index:** `ASK_MODE_BENCHMARK_SUITE_INDEX.md`

| Test | Name | Notebook |
|------|------|----------|
| 1 | Explain Notebook | Pakistan housing (`113620421`) |
| 2 | Explain Cell | Pakistan housing, cell 17 |
| 3 | Debug Error | testing-ol (`112732919`), cell 31 |
| 4 | Empty Cell | Empty-cell fixture |

---

### Code mode (Tests 1–4)

Runnable Python + placement guidance; **prefetch reads only**, **no browser writes**.

```powershell
python testing/host/scripts/run_code_tests.py --live-llm
```

Single test:

```powershell
python testing/host/scripts/run_code_tests.py --live-llm --test 3
```

**Config:** `scripts/fyp_experiment_benchmarks_code.json`  
**Outputs:** `testing/host/data/logs/CODE_TEST_*_{results,summary,report}.{json,md}`  
**Suite index:** `CODE_MODE_BENCHMARK_SUITE_INDEX.md`

| Test | Name | Notebook |
|------|------|----------|
| 1 | Pipeline Generation | Pakistan housing — XGBoost cell |
| 2 | Cell Replacement | Pakistan housing — cell 21 |
| 3 | Feature Module | Pakistan housing — feature engineering |
| 4 | Empty Cell | Empty-cell fixture — cell 50 |

---

### Agentic mode (Tests 1–4)

Two-phase query → implement; **native tool_calls** dispatched (browser mocked in harness).

```powershell
python testing/host/scripts/run_agent_tests.py --live-llm
```

Single test:

```powershell
python testing/host/scripts/run_agent_tests.py --live-llm --only AGENT_TEST_1_ML_PIPELINE
```

Rebuild docs from saved JSON:

```powershell
python testing/host/scripts/run_agent_tests.py --regenerate
```

**Config:** `scripts/fyp_experiment_benchmarks_agent_tests.json`  
**Outputs:** `testing/host/data/logs/AGENT_TEST_*_{results,summary,report}.{json,md}`  
**Suite index:** `AGENT_TESTS_INDEX.md`

| Test | ID | Notebook |
|------|-----|----------|
| 1 | ML Pipeline | Pakistan housing — full pipeline dispatch |
| 2 | Error Repair | testing-ol |
| 3 | Massive Batch | housing-final (`119598996`) |
| 4 | Target Cell Dispatch | testing-ol — cell 31 |

---

### Generate combined dissertation report

After running one or more suites:

```powershell
python testing/host/scripts/generate_fyp_dissertation_report.py
```

Writes:

- `testing/host/data/logs/FYP_DISSERTATION_BENCHMARK_REPORT.md`
- `testing/host/data/logs/FYP_BENCHMARK_AGGREGATE.json`

A copy of the thesis-run report is in `results/combined/`.

---

## Harness vs live UI

These scripts use the **harness** path: they call `streaming._run_streaming_chat` in-process with browser tools mocked at the extension boundary. This matches the methodology in Chapter 3 (repeatable, no Chrome/Kaggle session required).

- `--live-llm` → real Cerebras GLM 4.7 API calls  
- Without `--live-llm` → dry run / mocked LLM (for plumbing checks only)

Trace logs during runs: `testing/host/data/logs/agentic_tool_trace.jsonl`

---

## Files in `scripts/` (reference copies)

| File | Purpose |
|------|---------|
| `run_ask_tests.py` | Ask suite runner + rubric scoring |
| `run_code_tests.py` | Code suite runner + placement/code checks |
| `run_agent_tests.py` | Agentic suite runner + tool-dispatch metrics |
| `fyp_experiment_runner.py` | Shared harness (`run_harness_case`, metrics) |
| `generate_fyp_dissertation_report.py` | Aggregate all three modes into one report |
| `fyp_experiment_benchmarks_ask.json` | Ask test prompts + rubric keywords |
| `fyp_experiment_benchmarks_code.json` | Code test prompts + expected terms |
| `fyp_experiment_benchmarks_agent_tests.json` | Agentic test prompts |

Live copies used at runtime: `testing/host/scripts/` (keep in sync if you edit configs here).

---

## Saved results (thesis run)

| Folder | Contents |
|--------|----------|
| `results/ask/` | 4 tests × (results.json, summary.md, report.md) + suite index |
| `results/code/` | 4 tests × outputs + suite summary |
| `results/agentic/` | 4 tests × outputs + `AGENT_TESTS_INDEX.md` |
| `results/combined/` | `FYP_DISSERTATION_BENCHMARK_REPORT.md`, aggregate JSON, Ch.3 evaluation notes |

Show your teacher **`results/combined/FYP_DISSERTATION_BENCHMARK_REPORT.md`** for the full picture, or individual `*_summary.md` files per test.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Persistent snapshot not found` | Run `restore_notebooks.ps1` |
| `CEREBRAS_API_KEY` missing | Create `testing/host/.env` from repo `.env.example` |
| Rate limit / TPM errors | Wait and retry; check `CEREBRAS_TPM_LIMIT` in `.env` |
| Agentic tests fail immediately | Set `LLM_AGENTIC_ENABLED=1` in `.env` |

---

## Related paths in main project

```
testing/host/scripts/run_ask_tests.py
testing/host/scripts/run_code_tests.py
testing/host/scripts/run_agent_tests.py
testing/host/data/notebooks/persistent/*.json
testing/host/data/logs/
testing/host/streaming.py          ← chat + tool loop
testing/host/agentic_batch_executor.py
```
