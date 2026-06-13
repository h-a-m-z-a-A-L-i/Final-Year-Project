@echo off
setlocal EnableExtensions

REM Always run from this script's folder
cd /d "%~dp0"

REM Never hang on locked files (e.g. SQLite -shm/-wal while host is running)
set "GIT_TERMINAL_PROMPT=0"

where git >nul 2>&1
if errorlevel 1 (
  echo Git is not installed or not available in PATH.
  pause
  exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo This folder is not a Git repository.
  pause
  exit /b 1
)

set /p COMMIT_MSG=Enter commit message: 
if "%COMMIT_MSG%"=="" (
  echo Commit message cannot be empty.
  pause
  exit /b 1
)

echo Staging all changes...
git add -A

REM Never commit secrets or local runtime artifacts
call :unstage_sensitive
call :unstage_runtime

git diff --cached --quiet
if %errorlevel%==0 (
  echo No changes to commit.
  goto :sync_and_push
)

call :block_if_sensitive_staged
if errorlevel 1 (
  pause
  exit /b 1
)

echo Creating commit...
git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
  echo Commit failed.
  pause
  exit /b 1
)

:sync_and_push
call :discard_runtime_changes

echo Syncing with remote...
git fetch origin
if errorlevel 1 (
  echo Fetch failed.
  pause
  exit /b 1
)

git pull --rebase origin main
if errorlevel 1 (
  echo Pull failed. Another machine may have pushed first, or you have conflicts.
  echo Fix conflicts, then run: git rebase --continue
  echo Or abort with: git rebase --abort
  pause
  exit /b 1
)

echo Pushing to remote...
git push origin main
if errorlevel 1 (
  echo Push failed.
  pause
  exit /b 1
)

echo Done. Commit and push completed successfully.
pause
exit /b 0

:unstage_path
REM %1 = path to protect. Keep staged delete (D), unstage add/modify (A/M).
git diff --cached --name-status -- "%~1" 2>nul | findstr /r /b "D" >nul
if errorlevel 1 git reset HEAD -- "%~1" 2>nul
exit /b 0

:unstage_sensitive
call :unstage_path .env
call :unstage_path "kaggle json"
for /f "delims=" %%f in ('git diff --cached --name-only ^| findstr /i /r "kaggle\\.json kernel-metadata\\.json \\.env\\."') do (
  call :unstage_path "%%f"
)
exit /b 0

:unstage_runtime
call :unstage_path testing/host/data/logs
call :unstage_path testing/host/data/sessions
call :unstage_path testing/host/data/meta/execution_state.json
call :unstage_path testing/host/data/meta/notebook_registry.json
call :unstage_path testing/host/data/meta/rate_limit_tracker.json
call :unstage_path testing/host/data/meta/hashes.json
call :unstage_path testing/host/data/meta/kernel_metadata
call :unstage_path testing/host/data/meta/kernel_slug_index.json
call :unstage_path testing/host/data/notebooks/live
for /f "delims=" %%f in ('git diff --cached --name-only ^| findstr /i /r "__pycache__ \\.pyc$ \\.sqlite3-shm$ \\.sqlite3-wal$"') do (
  call :unstage_path "%%f"
)
exit /b 0

:discard_runtime_changes
REM Reset tracked runtime files so pull/rebase is not blocked by the host process.
git restore testing/host/data/logs 2>nul
git restore testing/host/data/meta/execution_state.json 2>nul
git restore testing/host/data/meta/notebook_registry.json 2>nul
git restore testing/host/data/meta/rate_limit_tracker.json 2>nul
git restore testing/host/data/meta/hashes.json 2>nul
git restore testing/host/data/meta/kernel_metadata 2>nul
git restore testing/host/data/meta/kernel_slug_index.json 2>nul
git restore testing/host/data/notebooks/live 2>nul
exit /b 0

:block_if_sensitive_staged
set "BLOCKED=0"
for /f "delims=" %%f in ('git diff --cached --name-only ^| findstr /i /r "\\.env$ \\.env\\. kaggle\\.json kernel-metadata\\.json"') do (
  git diff --cached --name-status -- "%%f" | findstr /r /b "D" >nul
  if errorlevel 1 (
    echo ERROR: Sensitive file is staged and will not be committed: %%f
    set "BLOCKED=1"
  )
)
if "%BLOCKED%"=="1" (
  echo.
  echo Commit aborted. Secrets must stay local.
  exit /b 1
)
exit /b 0
