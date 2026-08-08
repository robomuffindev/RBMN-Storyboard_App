@echo off
cd /d "%~dp0"
set "PYEXE=runtime\mia\venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Re-rigging Duke with --use-normal then rendering extreme arm poses (a few minutes)...
"%PYEXE%" tools\usenormal_test.py %1
echo.
pause
