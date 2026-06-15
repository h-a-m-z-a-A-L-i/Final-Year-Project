$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Set-Location $repoRoot
python testing/host/scripts/monitor_agentic_tool_calls.py @args
