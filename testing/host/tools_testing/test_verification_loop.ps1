<#
.SYNOPSIS
  Loop-test all browser verification tools via tools_testing/run.py.

.DESCRIPTION
  Section A: dispatch_verify one-shots (capture → dispatch → verify).
  Section B: manual two-step dispatch then verify_* (-TwoStep / -TwoStepOnly).

  Prerequisites:
    1. python testing/host/host.py  (running)
    2. Chrome extension loaded; Kaggle notebook /edit tab open
    3. Adjust $Cell* indices below if your notebook layout differs

.EXAMPLE
  .\testing\host\tools_testing\test_verification_loop.ps1
.EXAMPLE
  .\testing\host\tools_testing\test_verification_loop.ps1 -Url "https://www.kaggle.com/code/you/nb/edit" -TwoStep
.EXAMPLE
  .\testing\host\tools_testing\test_verification_loop.ps1 -TwoStepOnly -Step2Only
#>

param(
    [string]$Url = "https://www.kaggle.com/code/codekey/testing-ol/edit",
    [switch]$TwoStep,
    [switch]$TwoStepOnly,
    [switch]$Step2Only
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$RunPy    = Join-Path $RepoRoot "testing\host\tools_testing\run.py"

# --- adjust for your test notebook (1-based indices) ---
$SelectCell    = 1
$EditCell      = 1
$RunCell       = 1
$InsertIndex   = 1
$MarkdownIndex = 1
$DeleteCell    = 99   # use a disposable cell; run insert first if needed
$EditContent   = "x = 1  # verify-loop-test"
$InsertDir     = "below"

function Invoke-ToolTest([string]$Label, [string[]]$ToolArgs) {
    Write-Host "`n========== $Label ==========" -ForegroundColor Cyan
    Push-Location $RepoRoot
    try {
        $out = & python $RunPy @ToolArgs 2>&1 | Out-String
        Write-Host $out.TrimEnd()
        return $out
    } finally {
        Pop-Location
    }
}

Write-Host @"

Verification loop test
  URL: $Url
  Repo: $RepoRoot

REQUIRED: host.py running + extension connected to that notebook tab.
  Start:  python testing/host/host.py
  Check:  python testing/host/tools_testing/run.py tabs

"@ -ForegroundColor Yellow

Invoke-ToolTest "preflight" @("check") | Out-Null
$null = Read-Host "Press Enter when ready (Ctrl+C to abort)"

if (-not $TwoStepOnly) {
    Write-Host "`n===== SECTION A: dispatch_verify one-shots (safe order) =====" -ForegroundColor Green
    # Order: select → edit → run → insert → markdown → delete (destructive last)
    Invoke-ToolTest "A1 select"      @("dispatch_verify", "url=$Url", "tool=select_cell_by_index",      "cell=$SelectCell")
    Invoke-ToolTest "A2 edit"        @("dispatch_verify", "url=$Url", "tool=edit_cell_by_index",        "cell=$EditCell", "content=$EditContent")
    Invoke-ToolTest "A3 run"         @("dispatch_verify", "url=$Url", "tool=run_cell",                  "cell=$RunCell")
    Invoke-ToolTest "A4 insert"      @("dispatch_verify", "url=$Url", "tool=insert_cell",               "index=$InsertIndex", "direction=$InsertDir")
    Invoke-ToolTest "A5 markdown"    @("dispatch_verify", "url=$Url", "tool=creating_markdown_by_index", "index=$MarkdownIndex")
    Invoke-ToolTest "A6 delete"      @("dispatch_verify", "url=$Url", "tool=delete_by_index",           "cell=$DeleteCell")
}

if ($TwoStep -or $TwoStepOnly) {
    Write-Host "`n===== SECTION B: two-step dispatch → verify_* =====" -ForegroundColor Green
    if (-not $Step2Only) { Write-Host "(dispatch step; use -Step2Only to verify only)" -ForegroundColor DarkGray }

    # select: no baseline needed
    if (-not $Step2Only) {
        Invoke-ToolTest "B1 dispatch select" @("select_cell_by_index", "url=$Url", "cell=$SelectCell")
    }
    Invoke-ToolTest "B1 verify select" @("verify_select_cell", "url=$Url", "cell=$SelectCell")

    if (-not $Step2Only) {
        Invoke-ToolTest "B2 dispatch edit" @("edit_cell_by_index", "url=$Url", "cell=$EditCell", "content=$EditContent")
    }
    Invoke-ToolTest "B2 verify edit" @("verify_edit_cell", "url=$Url", "cell=$EditCell", "content=$EditContent")

    # run/insert/markdown/delete need pre-dispatch baseline for verify; use Section A for full flow.
    $baselineTools = @(
        @{ Name = "run";      Dispatch = @("run_cell", "url=$Url", "cell=$RunCell"); Verify = @("verify_run_cell", "url=$Url", "cell=$RunCell") },
        @{ Name = "insert";   Dispatch = @("insert_cell", "url=$Url", "index=$InsertIndex", "direction=$InsertDir"); Verify = @("verify_insert_cell", "url=$Url", "index=$InsertIndex", "direction=$InsertDir") },
        @{ Name = "markdown"; Dispatch = @("creating_markdown_by_index", "url=$Url", "index=$MarkdownIndex"); Verify = @("verify_creating_markdown", "url=$Url", "index=$MarkdownIndex") },
        @{ Name = "delete";   Dispatch = @("delete_by_index", "url=$Url", "cell=$DeleteCell"); Verify = @("verify_delete_cell", "url=$Url", "cell=$DeleteCell") }
    )
    $i = 3
    foreach ($t in $baselineTools) {
        if (-not $Step2Only) {
            Write-Host "`n--- B$i $($t.Name): dispatch (verify commented; needs before_file or Section A) ---" -ForegroundColor DarkYellow
            Invoke-ToolTest "B$i dispatch $($t.Name)" $t.Dispatch
            Write-Host "# verify: python testing/host/tools_testing/run.py $($t.Verify -join ' ')" -ForegroundColor DarkGray
        } else {
            Invoke-ToolTest "B$i verify $($t.Name)" $t.Verify
        }
        $i++
    }
}

Write-Host "`nDone. Re-run: python testing/host/tools_testing/run.py tabs" -ForegroundColor Green
