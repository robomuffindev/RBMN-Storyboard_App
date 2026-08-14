@echo off
REM Build the frontend the way the app actually ships it, and FAIL LOUDLY.
REM
REM run.bat rebuilds with `npx vite build >nul 2>&1` and, on failure, serves the
REM OLD dist/ with only a one-line [WARN]. The app then looks fine, the version
REM banner is right, and the change you just made is simply not there. That trap
REM has cost this project real debugging time, so here is the same build with
REM its output visible and its exit code meaningful.
REM
REM This runs `npx vite build`, NOT `npm run build` — because `npx vite build`
REM is exactly what run.bat ships, so a build that passes here is the build the
REM app actually serves.
REM
REM ⚠ v1.276.41 CORRECTION: this comment used to say `npm run build` could
REM never work, because `tsc && vite build` hit 16 pre-existing type errors.
REM THOSE ARE NOW FIXED and `tsc --noEmit` is clean, so it would work. The
REM typecheck still runs below as INFORMATION rather than as a gate — a type
REM error should be visible without being able to stop a release, and vite
REM erases types anyway.
REM
REM Run it via the agent:  {"kind":"script","file":"build_frontend.bat"}
REM Then GREP dist\assets for a string only your change contains — a build that
REM exits 0 is necessary, not sufficient.
setlocal
cd /d "%~dp0\..\frontend" || exit /b 1
echo === npx vite build in %CD% ===
call npx --yes vite build
if errorlevel 1 (
  echo *** BUILD FAILED — dist\ is UNCHANGED and the app is still serving the old bundle ***
  exit /b 1
)
echo === BUILD OK ===
dir /b /o-d dist\assets\index-*.js
echo.
echo === typecheck (informational — pre-existing errors, NOT a gate) ===
call npx --yes tsc --noEmit
exit /b 0
