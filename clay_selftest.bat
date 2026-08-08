@echo off
cd /d "%~dp0"
set "PYEXE=runtime\mia\venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Running clay self-test in the MIA venv... (renders + weight audit, ~1-2 min)
"%PYEXE%" tools\clay_selftest.py %1
echo.
pause
