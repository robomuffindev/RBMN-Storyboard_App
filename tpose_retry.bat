@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Re-running the T-pose EDIT on one saved view (1 worker image per seed)...
"%PYEXE%" tools\tpose_retry.py %*
echo.
pause
