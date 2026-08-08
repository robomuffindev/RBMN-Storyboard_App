@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Rendering fat/short parametric mannequin grid (MakeHuman morphs)...
"%PYEXE%" tools\mannequin_test.py
echo.
pause
