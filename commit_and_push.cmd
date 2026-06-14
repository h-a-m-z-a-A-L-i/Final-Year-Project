@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Always run from this script's folder
cd /d "%~dp0"

REM Never hang on locked files (e.g. SQLite -shm/-wal while host is running)
set "GIT_TERMINAL_PROMPT=0"
set "GIT_OPTIONAL_LOCKS=0"

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

for /f "delims=" %%b in ('git branch --show-current 2^>nul') do set "BRANCH=%%b"
if not defined BRANCH (
  echo Could not determine the current branch.
  pause
  exit /b 1
)

set /p COMMIT_MSG=Enter commit message: 
if "!COMMIT_MSG!"=="" (
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
if not errorlevel 1 (
  echo No changes to commit.
  goto :sync_and_push
)

call :block_if_sensitive_staged
if errorlevel 1 (
  pause
  exit /b 1
)

echo Creating commit...
git commit -m "!COMMIT_MSG!"
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

REM --autostash temporarily shelves dirty tracked files (e.g. live SQLite DB)
REM while the host is running, then restores them after rebase.
git pull --rebase --autostash origin !BRANCH!
if errorlevel 1 (
  echo Pull failed. Another machine may have pushed first, or you have conflicts.
  echo Fix conflicts, then run: git rebase --continue
  echo Or abort with: git rebase --abort
  pause
  exit /b 1
)

echo Pushing to remote...
git push origin !BRANCH!
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
if errorlevel 1 git restore --staged -- "%~1" >nul 2>&1
exit /b 0

:is_env_example
REM %1 = path. Return 0 when it is the safe template file.
if /i "%~1"==".env.example" exit /b 0
if /i "%~nx1"==".env.example" exit /b 0
exit /b 1

:unstage_sensitive
call :unstage_path .env
call :unstage_path "kaggle json"
for /f "delims=" %%f in ('git diff --cached --name-only ^| findstr /i /r "kaggle\.json kernel-metadata\.json \\.env\\."') do (
  call :is_env_example "%%f"
  if errorlevel 1 call :unstage_path "%%f"
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
for /f "delims=" %%f in ('git diff --cached --name-only ^| findstr /i /r "__pycache__ \\.pyc$ \\.sqlite3-shm$ \\.sqlite3-wal$ \\.sqlite3$"') do (
  call :unstage_path "%%f"
)
exit /b 0

:discard_runtime_changes
REM Best-effort reset of runtime files before sync.
git restore testing/host/data/logs >nul 2>&1
git restore testing/host/data/sessions >nul 2>&1
git restore testing/host/data/meta/execution_state.json >nul 2>&1
git restore testing/host/data/meta/notebook_registry.json >nul 2>&1
git restore testing/host/data/meta/rate_limit_tracker.json >nul 2>&1
git restore testing/host/data/meta/hashes.json >nul 2>&1
git restore testing/host/data/meta/kernel_metadata >nul 2>&1
git restore testing/host/data/meta/kernel_slug_index.json >nul 2>&1
git restore testing/host/data/notebooks/live >nul 2>&1
for /f "delims=" %%f in ('git ls-files ^| findstr /i /r "__pycache__/ \\.pyc$"') do (
  git restore -- "%%f" >nul 2>&1
)
exit /b 0

:block_if_sensitive_staged
set "BLOCKED=0"
for /f "delims=" %%f in ('git diff --cached --name-only ^| findstr /i /r "\\.env$ \\.env\\. kaggle\.json kernel-metadata\.json"') do (
  call :is_env_example "%%f"
  if errorlevel 1 (
    git diff --cached --name-status -- "%%f" | findstr /r /b "D" >nul
    if errorlevel 1 (
      echo ERROR: Sensitive file is staged and will not be committed: %%f
      set "BLOCKED=1"
    )
  )
)
if "!BLOCKED!"=="1" (
  echo.
  echo Commit aborted. Secrets must stay local.
  exit /b 1
)
exit /b 0
