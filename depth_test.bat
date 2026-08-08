@echo off
cd /d "%~dp0"
set "PYEXE=venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo Rendering DEPTH vs SHADED pose references (mannequin + rigged clay)...
"%PYEXE%" tools\depth_test.py %*
echo.
pause
