@echo off
chcp 65001 >nul
title Harven AI (Public)
echo.
echo  ==============================================
echo       Harven AI - Personal AI
echo       Public Mode | Cloudflare Tunnel
echo  ==============================================
echo.
echo  启动后将生成公网链接，可分享给朋友访问
echo  首次启动会自动下载 cloudflared (~25MB)
echo.

set "PROJECT_DIR=%~dp0"

:: 自动检测 Python（同上）
set "PYTHON_EXE="
where python >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where python 2^>nul ^| findstr /v "WindowsApps"') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
    )
)
if not defined PYTHON_EXE (
    for %%d in (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "C:\Python313\python.exe"
    ) do (
        if exist %%d if not defined PYTHON_EXE set "PYTHON_EXE=%%d"
    )
)
if not defined PYTHON_EXE (
    if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
        set "PYTHON_EXE=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    )
)
if not defined PYTHON_EXE (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

:: 检查并安装依赖
"%PYTHON_EXE%" -c "import flask" 2>nul
if %errorlevel% neq 0 (
    echo [SETUP] 正在安装依赖...
    "%PYTHON_EXE%" -m pip install -r "%PROJECT_DIR%requirements.txt" --quiet
)

cd /d "%PROJECT_DIR%"

echo [START] Starting Harven AI + Cloudflare Tunnel...
echo [START] 公网链接将在启动后显示...
echo.
set HARVEN_TUNNEL=1
"%PYTHON_EXE%" web_app.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Exit code %errorlevel%
    echo.
    pause
)
