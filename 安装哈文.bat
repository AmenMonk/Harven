@echo off
:: ============================================================
::  Harven AI - Install Wizard
::  #836 rework (2026-09-21):
::   - Python fallback chain: bundled -> py -> python -> common
::     paths -> winget auto-install -> clear manual instructions
::   - All messages ASCII-only (UTF-8 Chinese inside a .bat turns
::     into mojibake under the GBK console). Chinese guide: README.txt
::   - Desktop shortcut resolves the launcher by wildcard, so it
::     works no matter where the folder is extracted
:: ============================================================
title Harven AI - Install Wizard
cd /d "%~dp0"

:: ---------- resolve launcher (startup bat) ----------
set "LAUNCH_BAT="
for %%F in ("%~dp0*HarvenAI.bat") do set "LAUNCH_BAT=%%~fF"
if not defined LAUNCH_BAT for /f "delims=" %%F in ('powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%~dp0' -Filter '*.bat' | Where-Object { $_.Name -match 'HarvenAI' } | Select-Object -First 1 -ExpandProperty FullName"') do set "LAUNCH_BAT=%%F"

:: ---------- [1/5] locate Python ----------
echo.
echo  [1/5] Looking for Python...
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
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
) do if not defined PYTHON if exist %%P set "PYTHON=%%~P"

if not defined PYTHON (
    echo   Not found yet. Trying winget auto-install, 1-2 minutes...
    winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe"
    if not defined PYTHON if exist "%ProgramFiles%\Python313\python.exe" set "PYTHON=%ProgramFiles%\Python313\python.exe"
)

if not defined PYTHON (
    color 0C
    echo.
    echo  ==============================================================
    echo   Python 3.10+ was not found on this computer.
    echo  ==============================================================
    echo.
    echo   HOW TO FIX - only 2 steps:
    echo.
    echo   1. Open   https://www.python.org/downloads/
    echo      and download Python 3.13, 64-bit installer.
    echo.
    echo   2. Run the installer. On the FIRST screen, TICK the box
    echo.
    echo        [x] Add python.exe to PATH
    echo.
    echo      ...this box is IMPORTANT, then click "Install Now".
    echo.
    echo   When finished, double-click the install file again.
    echo   Full illustrated guide: README.txt
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('"%PYTHON%" --version 2^>^&1') do echo   OK: Python %%v found

echo.
echo  ==============================================================
echo    Harven AI  -  Install Wizard
echo    One-Click Install + Desktop Shortcut
echo  ==============================================================
echo.

:: ---------- [2/5] create venv ----------
echo  [2/5] Creating virtual environment...
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PIP=%~dp0.venv\Scripts\pip.exe"
if exist "%VENV_PY%" (
    echo        Virtual environment already exists
) else (
    "%PYTHON%" -m venv .venv
    if errorlevel 1 (
        echo   ERROR: failed to create the virtual environment.
        pause
        exit /b 1
    )
    echo        Virtual environment created
)

:: ---------- [3/5] dependencies ----------
echo  [3/5] Installing dependencies...
:: Pin pip to known-good 25.3 via Aliyun mirror: pip 26.1.x User-Agent gets
::   HTTP 403 from the Tsinghua mirror (verified 2026-11-04 by test install).
::   Aliyun index is set explicitly so installs never depend on the user machine
::   global pip config. Do not blind-upgrade pip.
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
    echo        First install takes 3-5 minutes, please wait...
    "%VENV_PIP%" install -r requirements.txt -i "%PIP_INDEX%"
    if errorlevel 1 (
        echo        WARNING: some packages failed, Harven will still try to run.
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
    echo        Dependencies installed
) else (
    echo        Dependencies are up-to-date
)

:: ---------- [3.5/5] full tier: bundled llama-cpp engine (offline wheel, #1425) ----------
:: TIER file is injected by the packer (lite / standard / full). Full tier ships
:: the vision-engine wheel in wheels\llama_cpp - install it locally, no network.
:: #1450 (2026-11-05) gap fix: the manual bat previously MISSED this whole
:: section - a full-tier manual install ended up without the vision engine.
set "TIER_NAME="
if exist "%~dp0TIER" set /p TIER_NAME=<"%~dp0TIER"
if /i "%TIER_NAME%"=="full" (
    if exist "%~dp0wheels\llama_cpp\llama_cpp_python-0.4.5+cu124-cp313-cp313-win_amd64.whl" (
        echo  [3.5/5] Installing bundled vision engine, no network needed...
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

:: ---------- [3.9/5] vision GPU self-check (#1449) ----------
:: Loud warning on silent CPU fallback: cu124/cu126 needs NVIDIA driver 551.61+.
:: Old drivers make llama.cpp / torch fall back to CPU with NO error at all.
if exist "%VENV_PY%" (
    if exist "%~dp0gpu_selfcheck.py" (
        "%VENV_PY%" "%~dp0gpu_selfcheck.py"
        if errorlevel 2 timeout /t 12 >nul
    )
)

:: ---------- [4/5] config ----------
echo  [4/5] Initializing configuration...
if not exist "config.yaml" (
    if exist "config.example.yaml" (
        copy "config.example.yaml" "config.yaml" >nul
        echo        config.yaml created
    ) else if exist "config.yaml.example" (
        copy "config.yaml.example" "config.yaml" >nul
        echo        config.yaml created
    )
) else (
    echo        config.yaml already exists
)
if not exist "data\sqlite\" mkdir "data\sqlite"
echo        Data directory ready

:: ---------- [5/5] desktop shortcut ----------
echo  [5/5] Creating desktop shortcut...
set "DUCK_ICON="
if exist "static\icons\duck.ico" set "DUCK_ICON=static\icons\duck.ico"
if not defined DUCK_ICON if exist "static\icon-duck.png" set "DUCK_ICON=static\icon-duck.png"
if not defined DUCK_ICON if exist "static\icons\duck_192.png" set "DUCK_ICON=static\icons\duck_192.png"

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "$sc = $ws.CreateShortcut(\"$desktop\Harven AI.lnk\");" ^
  "$sc.TargetPath = '%LAUNCH_BAT%';" ^
  "$sc.WorkingDirectory = '%~dp0';" ^
  "$sc.WindowStyle = 7;" ^
  "$sc.Description = 'Harven AI - Local AI Assistant';" ^
  "if ('%DUCK_ICON%' -ne '') { $sc.IconLocation = '%~dp0%DUCK_ICON%, 0' };" ^
  "$sc.Save();"
echo        Desktop shortcut created (duck icon)

:: ---------- done ----------
echo.
echo  ==============================================================
echo    Installation Complete!
echo  ==============================================================
echo    1. A "Harven AI" shortcut is now on your desktop
echo    2. Double-click it to start Harven
echo    3. Browser opens at  http://localhost:7000
echo    4. First login password: harven2026  (change it later)
echo  ==============================================================
echo.
:: Tiered one-click install (2026-11-03): no Y/N prompt, 10s countdown auto-launch.
::   Press any key during countdown to cancel. Zero required choices.
:: Tiered one-click install (2026-11-03): no Y/N prompt, 10s countdown auto-launch.
::   Press any key during countdown to cancel. Zero required choices.
echo    Launching Harven AI in 10 seconds... (press any key to cancel)
timeout /t 10 >nul
if errorlevel 1 (
    echo    Launch cancelled. Start Harven later with the desktop shortcut.
) else (
    if defined LAUNCH_BAT start "" "%LAUNCH_BAT%"
)
exit /b 0
