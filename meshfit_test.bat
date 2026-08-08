@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Fitting the pose mannequin to the character's 3D scan (default Duke)...
"%PYEXE%" tools\meshfit_test.py %*
echo.
pause
