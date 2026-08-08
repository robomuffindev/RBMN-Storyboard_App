@echo off
cd /d "%~dp0"
set "PYEXE=runtime\mia\venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Re-rigging with --no-rest then rendering the pose battery (a few minutes)...
"%PYEXE%" tools\rerig_test.py %1
echo.
pause
