@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  venv\Scripts\python.exe tools\rbmn_diag.py
) else (
  python tools\rbmn_diag.py
)
echo.
pause
