@echo off
rem Zero-setup launcher (Windows): runs the Orchestra Engine with the repo's venv,
rem no activation needed. Mirrors ./orchestra on POSIX. Works from cmd.exe and
rem PowerShell -- type `orchestra` (or `.\orchestra`) from the repo root.
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo No venv found. Set it up first:>&2
  echo   python bootstrap.py>&2
  exit /b 1
)
"%PY%" -m orchestra_engine %*
exit /b %errorlevel%
