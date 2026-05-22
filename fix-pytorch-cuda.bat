@echo off
setlocal enabledelayedexpansion
title Fix PyTorch CUDA - RBMN Storyboard App
color 0A

echo ============================================================
echo   PyTorch CUDA Fix
echo   Checks your PyTorch install and reinstalls with GPU support
echo ============================================================
echo.

:: ── Check venv exists ──────────────────────────────────────────
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] No venv found. Run install.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

:: ── Check nvidia-smi for GPU info ──────────────────────────────
echo [1/4] Checking GPU...
where nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] nvidia-smi not found. No NVIDIA GPU detected.
    echo         This fix requires an NVIDIA GPU with CUDA support.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('nvidia-smi --query-gpu=name --format=csv,noheader 2^>^&1') do set "GPU=%%i"
echo         Found: %GPU%

:: Grab CUDA version from nvidia-smi
for /f "tokens=9 delims= " %%i in ('nvidia-smi ^| findstr "CUDA Version"') do set "CUDA_VER=%%i"
echo         CUDA Driver: %CUDA_VER%

:: ── Check current PyTorch status ───────────────────────────────
echo.
echo [2/4] Checking current PyTorch...

echo import sys > "%TEMP%\rbmn_cudacheck.py"
echo try: >> "%TEMP%\rbmn_cudacheck.py"
echo     import torch >> "%TEMP%\rbmn_cudacheck.py"
echo     cuda = torch.cuda.is_available() >> "%TEMP%\rbmn_cudacheck.py"
echo     print(f"         PyTorch {torch.__version__}") >> "%TEMP%\rbmn_cudacheck.py"
echo     print(f"         CUDA available: {'YES' if cuda else 'NO'}") >> "%TEMP%\rbmn_cudacheck.py"
echo     if cuda: >> "%TEMP%\rbmn_cudacheck.py"
echo         print(f"         CUDA device: {torch.cuda.get_device_name(0)}") >> "%TEMP%\rbmn_cudacheck.py"
echo         print("") >> "%TEMP%\rbmn_cudacheck.py"
echo         print("         PyTorch already has CUDA support! No fix needed.") >> "%TEMP%\rbmn_cudacheck.py"
echo         sys.exit(0) >> "%TEMP%\rbmn_cudacheck.py"
echo     else: >> "%TEMP%\rbmn_cudacheck.py"
echo         print(f"         Build: {torch.__version__} (CPU only)") >> "%TEMP%\rbmn_cudacheck.py"
echo         sys.exit(2) >> "%TEMP%\rbmn_cudacheck.py"
echo except ImportError: >> "%TEMP%\rbmn_cudacheck.py"
echo     print("         PyTorch not installed") >> "%TEMP%\rbmn_cudacheck.py"
echo     sys.exit(3) >> "%TEMP%\rbmn_cudacheck.py"

python "%TEMP%\rbmn_cudacheck.py"
set "RESULT=%errorlevel%"
del "%TEMP%\rbmn_cudacheck.py" 2>nul

if %RESULT% equ 0 (
    echo.
    pause
    exit /b 0
)

:: ── Determine correct CUDA index URL ───────────────────────────
echo.
echo [3/4] Selecting CUDA toolkit version...

:: Default to cu121, use cu124 if driver supports 12.4+
set "CUDA_INDEX=cu121"
set "CUDA_LABEL=12.1"
if defined CUDA_VER (
    echo %CUDA_VER% | findstr /r "12\.[4-9]" >nul 2>&1
    if !errorlevel! equ 0 (
        set "CUDA_INDEX=cu124"
        set "CUDA_LABEL=12.4"
    )
    echo %CUDA_VER% | findstr /r "12\.[1-3]" >nul 2>&1
    if !errorlevel! equ 0 (
        set "CUDA_INDEX=cu121"
        set "CUDA_LABEL=12.1"
    )
)
echo         Using PyTorch CUDA %CUDA_LABEL% (%CUDA_INDEX%)

:: ── Reinstall PyTorch with CUDA ────────────────────────────────
echo.
echo [4/4] Reinstalling PyTorch with CUDA support...
echo         This may take several minutes depending on your connection.
echo.

pip uninstall torch torchvision torchaudio -y
if %errorlevel% neq 0 (
    echo [WARN]  Uninstall had issues, continuing anyway...
)

pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/%CUDA_INDEX%
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] PyTorch CUDA install failed. Try manually:
    echo         venv\Scripts\activate
    echo         pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/%CUDA_INDEX%
    pause
    exit /b 1
)

:: ── Verify ─────────────────────────────────────────────────────
echo.
echo ============================================================
echo   Verifying installation...
echo ============================================================

echo import torch > "%TEMP%\rbmn_cudaverify.py"
echo cuda = torch.cuda.is_available() >> "%TEMP%\rbmn_cudaverify.py"
echo print(f"  PyTorch {torch.__version__}") >> "%TEMP%\rbmn_cudaverify.py"
echo print(f"  CUDA: {'YES' if cuda else 'NO'}") >> "%TEMP%\rbmn_cudaverify.py"
echo if cuda: >> "%TEMP%\rbmn_cudaverify.py"
echo     print(f"  GPU: {torch.cuda.get_device_name(0)}") >> "%TEMP%\rbmn_cudaverify.py"
echo     print(f"  VRAM: {torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GB") >> "%TEMP%\rbmn_cudaverify.py"

python "%TEMP%\rbmn_cudaverify.py"
del "%TEMP%\rbmn_cudaverify.py" 2>nul

echo.
echo ============================================================
echo   Done! Local Whisper and Demucs will now use your GPU.
echo   Run the app with: run.bat
echo ============================================================
echo.
pause
