@echo off
title Robomuffin Idea Factory

:: Activate venv
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

:: ── Auto-rebuild frontend if source is newer than dist ──────────
:: This ensures updates from git pull are always picked up.
:: Vite build is fast (~3-5s) when nothing changed.
if exist "frontend\src" (
    echo [BUILD] Rebuilding frontend...
    pushd frontend
    call npx --yes vite build >nul 2>&1
    if %errorlevel% neq 0 (
        echo [WARN]  Frontend build failed. Using previous build if available.
        echo         To debug: cd frontend ^& npx vite build
    ) else (
        echo [BUILD] Frontend ready.
    )
    popd
) else (
    echo [WARN]  frontend\src not found — skipping build.
)

:: Pass any arguments through (--mode browser, --mode server, etc.)
python run.py %*
