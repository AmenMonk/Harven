@echo off
:: ============================================================
::  Harven AI - Silent Install (used by the EXE setup wizard)
::  Same logic as the manual install bat, but:
::   - no pause on success (setup wizard closes the window)
::   - pause only on fatal error so the user can read it
::   - no auto-launch (the setup wizard finish page handles it)
::  All messages ASCII-only (GBK console mojibake safety).
:: ============================================================
title Harven AI - Installing dependencies...
cd /d "%~dp0"

:: ---------- [1/4] locate Python (bundled -> py -> python -> common paths -> winget)
set "PYTHON="
if exist "%~dp0python\python.exe" set "PYTHON=%~dp0python\python.exe"

if not defined PYTHON for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%i"

if not defined PYTHON (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON=python"
)

if not defined PYTHON for %%P in (
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "C://Python313//python.exe"
    "C://Python312//python.exe"
) do if not defined PYTHON if exist %%P set "PYTHON=%%~P"

if not defined PYTHON (
    echo   Python not found. Trying winget auto-install, 1-2 minutes...
    winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe"
    if not defined PYTHON if exist "%ProgramFiles%\Python313\python.exe" set "PYTHON=%ProgramFiles%\Python313\python.exe"
)

if not defined PYTHON (
    color 0C
    echo.
    echo  =============================================================
    echo   Python 3.10+ was not found on this computer.
    echo   Please install Python from https://www.python.org/downloads/
    echo   TICK "Add python.exe to PATH" on the first screen,
    echo   then run this installer again.
    echo  =============================================================
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('"%PYTHON%" --version 2^>^&1') do echo   [1/4] Python %%v found

:: ---------- [2/4] create venv
echo   [2/4] Creating virtual environment...
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PIP=%~dp0.venv\Scripts\pip.exe"
if exist "%VENV_PY%" (
    echo         Virtual environment already exists
) else (
    "%PYTHON%" -m venv .venv
    if errorlevel 1 (
        color 0C
        echo   ERROR: failed to create the virtual environment.
        pause
        exit /b 1
    )
)

:: ---------- [3/4] dependencies
:: Pin pip to known-good 25.3 via Aliyun mirror: pip 26.1.x User-Agent gets
::   HTTP 403 from the Tsinghua mirror (verified 2026-11-04 by test install).
set "PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/"
"%VENV_PIP%" install --quiet "pip==25.3" -i "%PIP_INDEX%" >nul 2>&1

set "MARKER_FILE=.venv\.harven_deps_installed"
set "NEED_INSTALL=0"
if not exist "%MARKER_FILE%" set "NEED_INSTALL=1"
if %NEED_INSTALL%==0 (
    for %%F in (requirements.txt) do (
        for %%M in ("%MARKER_FILE%") do (
            if "%%~tF" gtr "%%~tM" set "NEED_INSTALL=1"
        )
    )
)

if %NEED_INSTALL%==1 (
    echo   [3/4] Installing dependencies, first time takes 3-5 minutes...
    "%VENV_PIP%" install -r requirements.txt -i "%PIP_INDEX%"
    if errorlevel 1 (
        color 0C
        echo   ERROR: dependency installation failed. Check your network
    echo   connection and run this installer again.
        pause
        exit /b 1
    )
    echo installed> "%MARKER_FILE%"

    REM GPU check (#1438): PyPI ships CPU-only torch on Windows by default.
    REM With an NVIDIA GPU install the CUDA build so the vision engine can
    REM use VRAM - the memory guard needs torch.cuda.is_available == True.
    REM Best effort: a failed download never blocks the installation.
    REM Own marker (.harven_cuda_torch): an interrupted torch download is
    REM retried on the next installer run (deps marker does not cover this).
    if not exist ".venv\.harven_cuda_torch" (
        nvidia-smi >nul 2>&1
        if not errorlevel 1 (
            echo        NVIDIA GPU detected - downloading CUDA torch, 2-4 GB, may take a while...
            "%VENV_PIP%" install "torch==2.12.1" --index-url https://download.pytorch.org/whl/cu126
            if errorlevel 1 (
                echo   WARN: CUDA torch download failed - keeping CPU torch. Vision still works, just slower.
                echo   RUN THIS INSTALLER AGAIN to resume the CUDA torch download.
            ) else (
                echo installed> ".venv\.harven_cuda_torch"
                "%VENV_PY%" -c "import torch; print(torch.__version__, torch.cuda.is_available())"
            )
        ) else (
            echo none> ".venv\.harven_cuda_torch"
        )
    )
) else (
    echo   [3/4] Dependencies are up-to-date
)

:: ---------- [3.5/4] full tier: bundled llama-cpp engine (offline wheel, #1425)
:: TIER file is injected by the packer (lite / standard / full). Full tier ships
:: the vision-engine wheel in wheels\llama_cpp - install it locally, no network.
set "TIER_NAME="
if exist "%~dp0TIER" set /p TIER_NAME=<"%~dp0TIER"
if /i "%TIER_NAME%"=="full" (
    if exist "%~dp0wheels\llama_cpp\llama_cpp_python-0.4.5+cu124-cp313-cp313-win_amd64.whl" (
        echo   [3.5/4] Installing bundled vision engine, no network needed...
        "%VENV_PIP%" install --quiet --no-deps --no-index --find-links "%~dp0wheels\llama_cpp" diskcache llama-cpp-python
        if errorlevel 1 (
            echo   WARN: bundled vision engine install failed - photo-understanding
            echo   will use the cloud or Ollama channel instead. Installation continues.
        ) else (
            "%VENV_PY%" -c "import llama_cpp" >nul 2>&1
            if errorlevel 1 (
                echo   WARN: vision engine imported with errors on this machine.
                echo   Photo-understanding will use the cloud or Ollama channel instead.
            ) else (
                echo         Vision engine ready.
                REM #1449: the cu124 wheel ships WITHOUT the CUDA runtime DLLs.
                REM Copy them from torch\lib so ggml-cuda.dll can load, else the
                REM engine silently falls back to CPU (verified 2026-11-05).
                if exist ".venv\Lib\site-packages\torch\lib\cudart64_12.dll" (
                    copy /y ".venv\Lib\site-packages\torch\lib\cudart64_12.dll" ".venv\Lib\site-packages\llama_cpp\lib\" >nul 2>&1
                    copy /y ".venv\Lib\site-packages\torch\lib\cublas64_12.dll" ".venv\Lib\site-packages\llama_cpp\lib\" >nul 2>&1
                    copy /y ".venv\Lib\site-packages\torch\lib\cublasLt64_12.dll" ".venv\Lib\site-packages\llama_cpp\lib\" >nul 2>&1
                )
            )
        )
    ) else (
        echo   WARN: full-tier vision engine file not found - skipping. Installation continues.
    )
)

:: ---------- [3.9/4] vision GPU self-check (#1449) ----------
:: Loud warning on silent CPU fallback: cu124/cu126 needs NVIDIA driver 551.61+.
:: Old drivers make llama.cpp / torch fall back to CPU with NO error at all.
if exist "%VENV_PY%" (
    if exist "%~dp0gpu_selfcheck.py" (
        "%VENV_PY%" "%~dp0gpu_selfcheck.py"
        if errorlevel 2 timeout /t 12 >nul
    )
)

:: ---------- [4/4] config + data dirs
echo   [4/4] Initializing configuration...
if not exist "config.yaml" (
    if exist "config.example.yaml" (
        copy "config.example.yaml" "config.yaml" >nul
    ) else if exist "config.yaml.example" (
        copy "config.yaml.example" "config.yaml" >nul
    )
)
if not exist "data\sqlite\" mkdir "data\sqlite"

:: ---------- done (shortcut is created by the setup wizard)
echo.
echo   Setup finished. You can close this window.
timeout /t 3 >nul
exit /b 0
