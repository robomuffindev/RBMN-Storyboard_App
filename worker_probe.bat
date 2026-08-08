@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Probing ComfyUI workers for nodes + model lists...
"%PYEXE%" tools\worker_probe.py %*
echo.
pause
