@echo off
setlocal

REM Always run from this script's folder
cd /d "%~dp0"

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

git diff --cached --quiet
if %errorlevel%==0 (
  echo No changes to commit.
  pause
  exit /b 0
)

echo Creating commit...
git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
  echo Commit failed.
  pause
  exit /b 1
)

echo Pushing to remote...
git push
if errorlevel 1 (
  echo Push failed.
  pause
  exit /b 1
)

echo Done. Commit and push completed successfully.
pause
exit /b 0
