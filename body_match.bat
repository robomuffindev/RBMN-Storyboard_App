@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Measuring build match: reference photo vs generated base (vs the 3D scan)...
"%PYEXE%" tools\body_match.py %*
echo.
pause
