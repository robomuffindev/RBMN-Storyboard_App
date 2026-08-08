@echo off
:: Measure how tight each image's crop actually is.
::   scripts\framing_probe.bat
:: Read-only. Needs the backend running on 127.0.0.1:8899.
pushd "%~dp0.."
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run install.bat first.
    popd
    pause
    exit /b 1
)
venv\Scripts\python.exe scripts\framing_probe.py %*
popd
pause
