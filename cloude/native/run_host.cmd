@echo off
setlocal

set SCRIPT_DIR=%~dp0
if not defined PYTHON_BIN (
  set PYTHON_BIN=python
)

"%PYTHON_BIN%" "%SCRIPT_DIR%..\step1_backend.py" 2>>"%SCRIPT_DIR%..\database\native_host.log"
