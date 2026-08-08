@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Replaying the last Klein pose graph against a real worker...
"%PYEXE%" tools\worker_run.py %*
echo.
pause
