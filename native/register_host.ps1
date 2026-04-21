param(
  [Parameter(Mandatory = $true)]
  [string]$ExtensionId,

  [string]$PythonBin = "python",
  [string]$HostName = "com.normalchrome.scraper"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$templatePath = Join-Path $scriptDir "$HostName.json"
$generatedManifestPath = Join-Path $scriptDir "$HostName.generated.json"
$generatedLauncherPath = Join-Path $scriptDir "run_host.generated.cmd"
$hostScriptPath = Join-Path $scriptDir "..\step1_backend.py"

if (-not (Test-Path $templatePath)) {
  throw "Template manifest not found: $templatePath"
}

if (-not (Test-Path $hostScriptPath)) {
  throw "Python host script not found: $hostScriptPath"
}

$launcherContent = @"
@echo off
setlocal
"$PythonBin" "$hostScriptPath"
"@
Set-Content -Path $generatedLauncherPath -Value $launcherContent -Encoding Ascii

$manifest = Get-Content -Path $templatePath -Raw | ConvertFrom-Json
$manifest.path = $generatedLauncherPath
$manifest.allowed_origins = @("chrome-extension://$ExtensionId/")

$manifest | ConvertTo-Json -Depth 5 | Set-Content -Path $generatedManifestPath -Encoding Ascii

$registryPath = "HKCU\Software\Google\Chrome\NativeMessagingHosts\$HostName"
& reg add $registryPath /ve /t REG_SZ /d $generatedManifestPath /f | Out-Null

Write-Host "Native host registered."
Write-Host "Manifest: $generatedManifestPath"
Write-Host "Launcher: $generatedLauncherPath"
Write-Host "Host name: $HostName"
