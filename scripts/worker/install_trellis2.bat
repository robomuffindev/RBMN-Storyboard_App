@echo off
REM One-shot ComfyUI-Trellis2 installer (RBMN Klein 2.0 workers).
REM Put install_trellis2.bat + install_trellis2.py in the ComfyUI portable root
REM (the folder with python_embeded\) OR in its ComfyUI\ subfolder, then run it.
setlocal
set "HERE=%~dp0"
set "PY="
if exist "%HERE%python_embeded\python.exe" set "PY=%HERE%python_embeded\python.exe"
if not defined PY if exist "%HERE%..\python_embeded\python.exe" set "PY=%HERE%..\python_embeded\python.exe"
if not defined PY if exist "%CD%\python_embeded\python.exe" set "PY=%CD%\python_embeded\python.exe"
if not defined PY if exist "%CD%\..\python_embeded\python.exe" set "PY=%CD%\..\python_embeded\python.exe"
if not defined PY (
  echo [FAIL] Could not find python_embeded\python.exe next to or above this script.
  echo        Put this .bat + install_trellis2.py in the ComfyUI portable root
  echo        ^(e.g. E:\ComfyMaster\V1\ComfyUI_windows_portable\^) and run it there.
  pause
  exit /b 1
)
echo Using embedded python: %PY%
"%PY%" "%HERE%install_trellis2.py" %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo [OK] Done - now RESTART ComfyUI and watch its startup console.) else (echo [FAIL] Installer exited with code %RC% - paste the log above to Claude.)
pause
exit /b %RC%
