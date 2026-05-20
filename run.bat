@echo off
title RBMN Storyboard App

:: Activate venv
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

:: Pass any arguments through (--mode browser, --mode server, etc.)
python run.py %*
