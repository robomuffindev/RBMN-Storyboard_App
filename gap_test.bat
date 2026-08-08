@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Measuring arm-torso WELDS on the rigged mesh (see HANDOVER_PROMPT.md sect.3)...
"%PYEXE%" tools\gap_test.py %*
echo.
pause
