@echo off
title RBMN Agent
:: Start this ONCE and leave the window open.
:: Claude drops jobs in scripts\_agent\inbox and reads answers from
:: scripts\_agent\outbox - no scripts to run, nothing to paste.
::
::   scripts\agent.bat                 normal
::   scripts\agent.bat --allow-shell   also allow arbitrary commands
::
:: The loop below is deliberate: a "reload" job exits the agent with code 7 so
:: it comes straight back on updated code, without anyone at the keyboard.
pushd "%~dp0.."
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run install.bat first.
    popd
    pause
    exit /b 1
)
:loop
venv\Scripts\python.exe scripts\agent\rbmn_agent.py %*
if %errorlevel% equ 7 (
    echo.
    echo [AGENT] reloading on updated code...
    echo.
    goto loop
)
popd
echo.
echo Agent stopped.
pause
