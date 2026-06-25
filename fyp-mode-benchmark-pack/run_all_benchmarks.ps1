# Run Ask, Code, and Agentic benchmark suites with live Cerebras LLM.
# Usage (from repo root):  .\fyp-mode-benchmark-pack\run_all_benchmarks.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "=== Restoring notebook snapshots ===" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "restore_notebooks.ps1")

if (-not (Test-Path "testing\host\.env")) {
    Write-Warning "testing/host/.env not found. Copy .env.example and set CEREBRAS_API_KEY before live runs."
}

Write-Host "`n=== Ask mode (Tests 1-4) ===" -ForegroundColor Cyan
python testing/host/scripts/run_ask_tests.py --live-llm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Code mode (Tests 1-4) ===" -ForegroundColor Cyan
python testing/host/scripts/run_code_tests.py --live-llm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Agentic mode (Tests 1-4) ===" -ForegroundColor Cyan
python testing/host/scripts/run_agent_tests.py --live-llm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Combined dissertation report ===" -ForegroundColor Cyan
python testing/host/scripts/generate_fyp_dissertation_report.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`nAll benchmarks complete." -ForegroundColor Green
Write-Host "Results: testing\host\data\logs\"
Write-Host "Report:  testing\host\data\logs\FYP_DISSERTATION_BENCHMARK_REPORT.md"
