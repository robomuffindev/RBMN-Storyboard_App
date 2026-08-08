@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Scoring every library pose for arm-inside-torso penetration...
"%PYEXE%" tools\pose_audit.py %*
echo.
pause
