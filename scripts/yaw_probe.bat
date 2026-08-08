@echo off
:: Measure which way the man is actually facing, in degrees.
::   scripts\yaw_probe.bat
::   scripts\yaw_probe.bat --framing full
:: Read-only. Needs the backend running on 127.0.0.1:8899.
pushd "%~dp0.."
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run install.bat first.
    popd
    pause
    exit /b 1
)
venv\Scripts\python.exe scripts\yaw_probe.py %*
popd
pause
