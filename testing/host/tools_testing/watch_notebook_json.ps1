# Watch persistent notebook JSON — prints inserted / edited / deleted cells.
# Prereq: host.py running (updates persistent JSON when notebook changes).
# Ctrl+C to stop.

param(
    [string]$Url = "https://www.kaggle.com/code/codekey/testing-ol/edit"
)

Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
python testing/host/tools_testing/run.py watch_notebook_json "url=$Url"
