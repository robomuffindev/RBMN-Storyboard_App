@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Collecting Klein pose-run diagnostics (refs, outputs, settings, log)...
"%PYEXE%" tools\pose_diag.py %*
echo.
pause
