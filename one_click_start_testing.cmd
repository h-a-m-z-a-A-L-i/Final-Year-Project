@echo off
setlocal

set "ROOT=%~dp0"
set "EXTENSION_ID=kpgffbnnihefokomkllcnenpdcllaapb"
set "PYTHON_BIN=python"

pushd "%ROOT%" >nul

echo [1/3] Checking Python...
where %PYTHON_BIN% >nul 2>&1
if errorlevel 1 (
  echo ERROR: Global Python was not found on PATH.
  goto :fail
)

echo [2/3] Installing/updating Python dependencies...
"%PYTHON_BIN%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 goto :fail

echo [3/3] Registering Native Messaging host for testing extension...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%testing\host\register.ps1" -ExtensionId "%EXTENSION_ID%" -PythonBin "%PYTHON_BIN%"
if errorlevel 1 goto :fail

echo.
echo One-click setup complete.
echo If Chrome is already open, click Reload once for the extension on chrome://extensions.
start "" chrome "chrome://extensions/?id=%EXTENSION_ID%"

popd >nul
exit /b 0

:fail
echo.
echo One-click setup failed. See the error output above.
popd >nul
exit /b 1
