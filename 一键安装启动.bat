@echo off
:: ★ 2026-08-20 fix4: 全英文 echo 避免中文 Windows 编码问题
:: 中文提示通过 PowerShell 输出（PowerShell 默认 UTF-16，跨平台稳定）
title Harven AI - Install ^& Launch
cd /d "%~dp0"

:: ★ 优先使用内置 Python（python\python.exe），其次系统 Python
set "PYTHON="
if exist "%~dp0python\python.exe" (
    set "PYTHON=%~dp0python\python.exe"
) else (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
    powershell -NoProfile -Command "Write-Host 'ERROR: Python not found!' -F Red; Write-Host 'Please download the complete installation package from GitHub Release.' -F Yellow"
    pause
    exit /b 1
)

:: ★ 中文标题（PowerShell 输出，避免 cmd 编码问题）
powershell -NoProfile -Command "Write-Host ''; Write-Host '===============================================================' -F Cyan; Write-Host '  Harven AI  -  Private Local AI Brain' -F Cyan; Write-Host '  Personal AI Brain v0.3.1 (with built-in Python 3.13)' -F Cyan; Write-Host '===============================================================' -F Cyan; Write-Host ''"

:: ============================================================
:: Step 1: Check Python
:: ============================================================
powershell -NoProfile -Command "Write-Host '[1/4] Checking Python environment...' -F Yellow"
for /f "tokens=2" %%v in ('%PYTHON% --version 2^>^&1') do powershell -NoProfile -Command "Write-Host '       Python %%v detected OK' -F Green"

:: ============================================================
:: Step 2: Create virtual environment
:: ============================================================
powershell -NoProfile -Command "Write-Host '[2/4] Preparing Python virtual environment...' -F Yellow"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PIP=%~dp0.venv\Scripts\pip.exe"
if not exist "%VENV_PY%" (
    powershell -NoProfile -Command "Write-Host '       Creating venv...' -F Gray"
    "%PYTHON%" -m venv .venv
    if %errorlevel% neq 0 (
        powershell -NoProfile -Command "Write-Host 'ERROR: Failed to create virtual environment' -F Red"
        pause
        exit /b 1
    )
    powershell -NoProfile -Command "Write-Host '       Virtual environment created OK' -F Green"
) else (
    powershell -NoProfile -Command "Write-Host '       Virtual environment exists OK' -F Green"
)

:: ============================================================
:: Step 3: Install dependencies
:: ============================================================
powershell -NoProfile -Command "Write-Host '[3/4] Installing Python dependencies...' -F Yellow"
"%VENV_PIP%" install --quiet --upgrade pip >nul 2>&1

set "MARKER_FILE=.venv\.harven_deps_installed"
set "NEED_INSTALL=0"
if not exist "%MARKER_FILE%" set "NEED_INSTALL=1"
if %NEED_INSTALL%==0 (
    for %%F in (requirements.txt) do (
        for %%M in (%MARKER_FILE%) do (
            if "%%~tF" gtr "%%~tM" set "NEED_INSTALL=1"
        )
    )
)

if %NEED_INSTALL%==1 (
    powershell -NoProfile -Command "Write-Host '       Installing (first run: 3-10 minutes, please wait)...' -F Gray"
    "%VENV_PIP%" install -r requirements.txt
    if %errorlevel% neq 0 (
        powershell -NoProfile -Command "Write-Host '       WARNING: Some dependencies failed, continuing...' -F Yellow"
    )
    echo installed > "%MARKER_FILE%"
    powershell -NoProfile -Command "Write-Host '       Dependencies installed OK' -F Green"
) else (
    powershell -NoProfile -Command "Write-Host '       Dependencies are up-to-date' -F Green"
)

:: ============================================================
:: Step 4: Initialize config + check Ollama + start
:: ============================================================
powershell -NoProfile -Command "Write-Host '[4/4] Initializing configuration...' -F Yellow"

if not exist "config.yaml" (
    if exist "config.yaml.example" (
        copy "config.yaml.example" "config.yaml" >nul
        powershell -NoProfile -Command "Write-Host '       config.yaml created from example' -F Green"
    )
) else (
    powershell -NoProfile -Command "Write-Host '       config.yaml exists, skipping' -F Green"
)

if not exist "data\sqlite\" mkdir "data\sqlite"
powershell -NoProfile -Command "Write-Host '       Data directory ready' -F Green"

:: Check Ollama (optional)
powershell -NoProfile -Command "Write-Host ''; Write-Host '------- Checking Ollama (optional) -------' -F Magenta"
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Write-Host '       Ollama not installed - you can still use External Brain mode!' -F Yellow; Write-Host '       Go to Settings > Model Manager > select External Brain > fill API Key' -F Gray; Write-Host '       For local mode: install Ollama from https://ollama.com/download/windows' -F Gray"
) else (
    powershell -NoProfile -Command "Write-Host '       Ollama is running OK (local model engine available)' -F Green"
)

:: Set domestic mirror for model downloads (acceleration)
if "%OLLAMA_HF_MIRROR%"=="" set "OLLAMA_HF_MIRROR=https://modelscope.cn"

:: ============================================================
:: Start Harven AI
:: ============================================================
powershell -NoProfile -Command "Write-Host ''; Write-Host '===============================================================' -F Green; Write-Host '  Starting Harven AI...' -F Green; Write-Host '  URL: http://localhost:7000' -F Green; Write-Host '  Press Ctrl+C to stop' -F Green; Write-Host '  Admin password: harven2026' -F Green; Write-Host '===============================================================' -F Green; Write-Host ''"

"%VENV_PY%" web_app.py

pause