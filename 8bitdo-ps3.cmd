@echo off
rem 8bitdo-ps3 command line - works from any folder, e.g.  8bitdo-ps3 status
set "PYTHONPATH=%~dp0"
"%~dp0.venv\Scripts\python.exe" -m bitdo_ps3 %*
