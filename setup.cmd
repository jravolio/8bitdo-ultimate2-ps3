@echo off
rem One-time setup: creates .venv next to this file and installs what 8bitdo-ps3 needs.
rem   setup.cmd          normal install
rem   setup.cmd dev      also the test tools (pytest, pyftpdlib)
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3 was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run setup.cmd again.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  %PY% -m venv .venv || exit /b 1
)
set "REQ=requirements.txt"
if /i "%~1"=="dev" set "REQ=requirements-dev.txt"
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r %REQ% || exit /b 1
echo.
echo Done. Next:  8bitdo-ps3 status
