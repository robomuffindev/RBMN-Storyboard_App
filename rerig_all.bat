@echo off
cd /d "%~dp0"
set "PYEXE=runtime\mia\venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Re-rigging all 3D-body characters with the no-rest fix (a couple minutes each)...
"%PYEXE%" tools\rerig_all.py
echo.
pause
