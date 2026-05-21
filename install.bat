@echo off
setlocal enabledelayedexpansion
title RBMN Storyboard App - Installer
color 0A

echo ============================================================
echo   RBMN Storyboard App - Installer
echo   Everything installs into a local venv - no system changes
echo ============================================================
echo.

set "ERRORS=0"

:: ============================================================
:: 1. CHECK PYTHON
:: ============================================================
echo [1/6] Checking Python...

where python >nul 2>&1
if %errorlevel% neq 0 goto :no_python

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set "PYVER=%%i"
echo         Found: %PYVER%

:: Write a temp script to check version range (avoids cmd.exe parsing issues)
echo import sys > "%TEMP%\rbmn_pycheck.py"
echo v = sys.version_info >> "%TEMP%\rbmn_pycheck.py"
echo sys.exit(0 if v.major == 3 and 10 ^<= v.minor ^<= 12 else 1) >> "%TEMP%\rbmn_pycheck.py"
python "%TEMP%\rbmn_pycheck.py" 2>nul
if %errorlevel% neq 0 (
    echo [WARN]  Python 3.10-3.12 recommended. You have %PYVER%.
    echo         Some dependencies may not work on other versions.
)
del "%TEMP%\rbmn_pycheck.py" 2>nul
goto :check_node

:no_python
echo [ERROR] Python not found on PATH.
echo         Install Python 3.10-3.12 from https://www.python.org/downloads/
echo         Make sure to check "Add Python to PATH" during install.
set /a ERRORS+=1

:: ============================================================
:: 2. CHECK NODE.JS
:: ============================================================
:check_node
echo.
echo [2/6] Checking Node.js...

where node >nul 2>&1
if %errorlevel% neq 0 goto :no_node

for /f "tokens=*" %%i in ('node --version 2^>^&1') do set "NODEVER=%%i"
echo         Found: Node %NODEVER%

where npm >nul 2>&1
if %errorlevel% neq 0 goto :no_npm

for /f "tokens=*" %%i in ('npm --version 2^>^&1') do set "NPMVER=%%i"
echo         Found: npm %NPMVER%
goto :check_ffmpeg

:no_node
echo [ERROR] Node.js not found on PATH.
echo         Install Node.js 18+ from https://nodejs.org/
set /a ERRORS+=1
goto :check_ffmpeg

:no_npm
echo [ERROR] npm not found. It should come with Node.js.
set /a ERRORS+=1

:: ============================================================
:: 3. CHECK FFMPEG
:: ============================================================
:check_ffmpeg
echo.
echo [3/6] Checking FFmpeg...

where ffmpeg >nul 2>&1
if %errorlevel% neq 0 goto :no_ffmpeg

echo         Found: FFmpeg
goto :check_errors

:no_ffmpeg
echo [WARN]  FFmpeg not found on PATH.
echo         Video assembly and audio processing require FFmpeg.
echo         Install from https://ffmpeg.org/download.html
echo         Or via:  winget install Gyan.FFmpeg
echo         Continuing without it for now...

:: ---- Bail if critical deps missing ----
:check_errors
echo.
if !ERRORS! equ 0 goto :deps_ok
echo ============================================================
echo [FATAL] !ERRORS! required tool(s) missing. Fix errors above and re-run.
echo ============================================================
pause
exit /b 1

:deps_ok

:: ============================================================
:: 4. CREATE PYTHON VENV + INSTALL BACKEND
:: ============================================================
echo [4/6] Setting up Python virtual environment...

if exist "venv\Scripts\activate.bat" (
    echo         Existing venv found - reusing it.
) else (
    echo         Creating venv...
    python -m venv venv
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)

:: Activate venv
call venv\Scripts\activate.bat

:: Upgrade pip
echo         Upgrading pip...
python -m pip install --upgrade pip >nul 2>&1

:: Install the project in editable mode
echo         Installing backend dependencies (this may take several minutes)...
pip install -e ".[dev]"
if %errorlevel% neq 0 (
    echo [WARN]  pip install reported errors. Checking core packages...
)

:: Verify critical imports via temp script
echo import fastapi > "%TEMP%\rbmn_corecheck.py"
echo import sqlmodel >> "%TEMP%\rbmn_corecheck.py"
echo import uvicorn >> "%TEMP%\rbmn_corecheck.py"
echo print("         Core packages OK") >> "%TEMP%\rbmn_corecheck.py"

echo         Verifying core packages...
python "%TEMP%\rbmn_corecheck.py" 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Core packages failed to install.
    echo         Try: venv\Scripts\activate then pip install -e ".[dev]"
    del "%TEMP%\rbmn_corecheck.py" 2>nul
    pause
    exit /b 1
)
del "%TEMP%\rbmn_corecheck.py" 2>nul

:: Check optional heavy deps
echo import torch > "%TEMP%\rbmn_torchcheck.py"
echo print("         PyTorch OK") >> "%TEMP%\rbmn_torchcheck.py"

python "%TEMP%\rbmn_torchcheck.py" 2>nul
if %errorlevel% neq 0 (
    echo [WARN]  PyTorch not installed. Audio analysis won't work yet.
    echo         To install later, run these in this folder:
    echo           venv\Scripts\activate
    echo           pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    echo           pip install demucs whisperx
)
del "%TEMP%\rbmn_torchcheck.py" 2>nul

:: Check gradio_client (needed for Whisper-WebUI remote transcription)
echo import gradio_client > "%TEMP%\rbmn_gradiocheck.py"
echo print("         gradio_client OK") >> "%TEMP%\rbmn_gradiocheck.py"

python "%TEMP%\rbmn_gradiocheck.py" 2>nul
if %errorlevel% neq 0 (
    echo [WARN]  gradio_client not installed. Remote Whisper-WebUI transcription won't work.
    echo         Installing gradio_client...
    pip install gradio_client >nul 2>&1
    python "%TEMP%\rbmn_gradiocheck.py" 2>nul
    if !errorlevel! neq 0 (
        echo [WARN]  gradio_client install failed. Install manually later:
        echo           venv\Scripts\activate
        echo           pip install gradio_client
    )
)
del "%TEMP%\rbmn_gradiocheck.py" 2>nul

:: ============================================================
:: 5. INSTALL FRONTEND
:: ============================================================
echo.
echo [5/6] Installing frontend dependencies...

pushd frontend

:: Always remove lockfile before install — it may contain platform-specific
:: binaries from a different OS (e.g. Linux rollup on a Windows machine).
:: npm will regenerate it correctly for the current platform.
if exist "package-lock.json" (
    echo         Removing existing lockfile (will regenerate for this platform^)...
    del package-lock.json
)

echo         Running npm install...
call npm install
if %errorlevel% neq 0 (
    echo [WARN]  npm install had issues. Check output above.
)

echo         Building frontend for production...
call npx --yes vite build
if %errorlevel% neq 0 (
    echo [WARN]  Frontend build failed. App can still run in dev mode.
    echo         To retry: cd frontend ^& npm run build
)

if exist "dist\index.html" (
    echo         Frontend build successful.
) else (
    echo [WARN]  Frontend dist\index.html not found.
)

popd

:: ============================================================
:: 6. SETUP CONFIG FILE
:: ============================================================
echo.
echo [6/6] Checking configuration...

if exist ".env" (
    echo         .env file exists - keeping your settings.
) else (
    copy .env.example .env >nul 2>&1
    echo         Created .env from template.
    echo         Edit it with your ComfyUI URLs and API keys:
    echo         %cd%\.env
)

:: ============================================================
:: DONE
:: ============================================================
echo.
echo ============================================================
echo   Installation complete!
echo ============================================================
echo.
echo   To run the app:
echo     run.bat                        Desktop window
echo     run.bat --mode browser         Opens in your browser
echo     run.bat --mode server          API only (port 8899)
echo.
echo   Or manually:
echo     venv\Scripts\activate
echo     python run.py
echo.
echo   Dev mode (hot reload):
echo     Terminal 1: venv\Scripts\activate
echo                 uvicorn backend.main:app --reload --port 8899
echo     Terminal 2: cd frontend
echo                 npm run dev
echo     Then open http://localhost:5173
echo.
echo   Edit .env with your ComfyUI server URLs before running!
echo ============================================================
echo.
pause
