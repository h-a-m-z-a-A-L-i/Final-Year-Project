param(
  [Parameter(Mandatory=$true)][string]$ExtensionId,
  [string]$PythonBin = "python"
)

$dir        = Split-Path -Parent $MyInvocation.MyCommand.Path
$hostScript = Join-Path $dir "host.py"
$launcher   = Join-Path $dir "launcher.cmd"
$manifest   = Join-Path $dir "host.generated.json"

# Build the launcher .cmd file that Chrome will call
Set-Content -Path $launcher -Encoding Ascii -Value @"
@echo off
"$PythonBin" "$hostScript"
"@

# Build the manifest with real paths
$json = Get-Content (Join-Path $dir "host.json") -Raw
$json = $json -replace '__LAUNCHER__',   ($launcher -replace '\\','\\')
$json = $json -replace '__EXTENSION_ID__', $ExtensionId
Set-Content -Path $manifest -Encoding Ascii -Value $json

# Register in Windows registry
reg add "HKCU\Software\Google\Chrome\NativeMessagingHosts\com.testing.tabprinter" `
    /ve /t REG_SZ /d $manifest /f | Out-Null

Write-Host "Registered! Manifest: $manifest"
Write-Host "Launcher:  $launcher"
