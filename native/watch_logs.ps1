param(
  [string]$LogPath = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent) "database\native_host.log")
)

if (-not (Test-Path $LogPath)) {
  New-Item -Path $LogPath -ItemType File -Force | Out-Null
}

Write-Host "Watching log: $LogPath"
Get-Content -Path $LogPath -Wait -Tail 30
