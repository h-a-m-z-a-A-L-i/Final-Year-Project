# Copy frozen notebook snapshots from this pack into the live host data directory.
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $PSScriptRoot "notebooks"
$Dst = Join-Path $RepoRoot "testing\host\data\notebooks\persistent"
$Live = Join-Path $RepoRoot "testing\host\data\notebooks\live"

New-Item -ItemType Directory -Force -Path $Dst, $Live | Out-Null

Get-ChildItem $Src -Filter "*.json" | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $Dst $_.Name) -Force
    Copy-Item $_.FullName (Join-Path $Live $_.Name) -Force
    Write-Host "Restored $($_.Name)"
}

Write-Host "`nDone. Snapshots ready in:"
Write-Host "  $Dst"
