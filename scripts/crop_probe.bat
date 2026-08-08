@echo off
:: Measure whether the subject is actually inside the frame, with a person mask.
::   scripts\crop_probe.bat
::   scripts\crop_probe.bat --id <dataset-id>
:: Read-only, CPU only. Needs the backend running on 127.0.0.1:8899.
pushd "%~dp0.."
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run install.bat first.
    popd
    pause
    exit /b 1
)
venv\Scripts\python.exe scripts\crop_probe.py %*
popd
pause
