@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM  RBMN Worker Helper — launcher
REM
REM  There is no install step. The helper is stdlib-only Python, so any
REM  python 3.9+ on the box will run it — including ComfyUI's embedded one.
REM  Copy rbmn_helper.py + rbmn_helper.bat anywhere on the worker and run this.
REM
REM    rbmn_helper.bat            serve on port 8765
REM    rbmn_helper.bat --probe    print what it found and exit (run this FIRST)
REM    rbmn_helper.bat --port 9000
REM ═══════════════════════════════════════════════════════════════════════
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ── find a python ──────────────────────────────────────────────────────
REM Prefer a real system python. ComfyUI's python_embeded works too, but it is
REM the interpreter we may be about to KILL, so a separate one is tidier.
set "PY="
for %%P in (python.exe py.exe) do (
  if not defined PY (
    where %%P >nul 2>&1 && set "PY=%%P"
  )
)
if not defined PY (
  for %%D in ("%CD%" "%CD%\.." "C:\ComfyUI" "D:\ComfyUI" "E:\ComfyUI") do (
    if not defined PY if exist "%%~D\python_embeded\python.exe" set "PY=%%~D\python_embeded\python.exe"
  )
)
if not defined PY (
  echo.
  echo   [FAIL] No python found on PATH and no ComfyUI python_embeded nearby.
  echo          Install python 3.9+ from python.org ^(tick "Add to PATH"^),
  echo          or run this from inside a ComfyUI portable folder.
  echo.
  pause
  exit /b 1
)

echo Using python: %PY%
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>nul
if errorlevel 1 (
  echo   [FAIL] %PY% is older than 3.9 — the helper needs 3.9+.
  pause
  exit /b 1
)

REM ── firewall note ──────────────────────────────────────────────────────
REM The app talks to this over the LAN. Windows will prompt the first time;
REM tick "Private networks". If you dismissed it, open the port by hand:
REM   netsh advfirewall firewall add rule name="RBMN Helper" dir=in ^
REM         action=allow protocol=TCP localport=8765

"%PY%" "%~dp0rbmn_helper.py" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo   [exit %RC%] — paste the output above to Claude.
pause
exit /b %RC%
