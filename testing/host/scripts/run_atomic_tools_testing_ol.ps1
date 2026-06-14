# Run each atomic browser tool smoke test against testing-ol notebook.
# Prerequisites:
#   1. python testing/host/host.py  (running in another terminal)
#   2. Chrome tab open at the exact URL below
#   3. Extension reloaded after JS changes

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$scripts = Join-Path $repoRoot "testing\host\scripts"
$url = "https://www.kaggle.com/code/codekey/testing-ol/edit"

Set-Location $repoRoot

Write-Host "Repo: $repoRoot"
Write-Host "URL:  $url"
Write-Host ""

function Invoke-Smoke {
    param(
        [string]$Name,
        [string[]]$ExtraArgs = @()
    )
    Write-Host "========== $Name ==========" -ForegroundColor Cyan
    python (Join-Path $scripts $Name) --url $url @ExtraArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host ""
}

Invoke-Smoke "smoke_select_cell.py" @("--cell-index", "1")
Invoke-Smoke "smoke_insert_cell.py" @("--index", "1", "--direction", "below")
Invoke-Smoke "smoke_edit_cell.py" @("--cell-index", "2", "--content", "print('atomic_smoke_edit')")
Invoke-Smoke "smoke_run_cell.py" @("--cell-index", "2")
Invoke-Smoke "smoke_creating_markdown.py" @("--index", "1")
# Delete last — destructive; adjust cell-index if your notebook layout differs
Invoke-Smoke "smoke_delete_cell.py" @("--cell-index", "2")

Write-Host "All atomic tool smoke tests passed." -ForegroundColor Green
