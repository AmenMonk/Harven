@echo off
chcp 65001 >nul 2>&1
title Harven AI 哈文
cd /d "%~dp0"

echo ╔════════════════════════════════════════╗
echo ║       Harven AI 哈文 正在启动...       ║
echo ╚════════════════════════════════════════╝
echo.

REM 检查端口5000是否被占用
netstat -ano | findstr ":7000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo ⚠️ 端口5000已被占用，可能哈文已在运行
    echo 正在打开浏览器...
    start http://127.0.0.1:7000
    timeout /t 3 >nul
    exit
)

REM ★ 2026-07-08: 首次启动检测——.venv不存在则自动创建
if not exist ".venv\Scripts\python.exe" (
    echo ╔════════════════════════════════════════╗
    echo ║     首次启动，正在初始化环境...        ║
    echo ║     需要联网下载依赖（约1GB，5-10分钟）║
    echo ╚════════════════════════════════════════╝
    echo.

    REM 检查系统Python
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo ❌ 系统未安装Python！
        echo 请先安装Python 3.13+：https://www.python.org/downloads/
        echo 安装时勾选"Add Python to PATH"
        pause
        exit
    )

    echo [1/3] 创建虚拟环境...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo ❌ 创建虚拟环境失败
        pause
        exit
    )
    echo ✅ 虚拟环境创建成功
    echo.

    echo [2/3] 安装依赖包（约1GB，请耐心等待）...
    ".venv\Scripts\pip.exe" install -r requirements.txt
    if %errorlevel% neq 0 (
        echo ❌ 依赖安装失败，请检查网络连接
        pause
        exit
    )
    echo ✅ 依赖安装完成
    echo.

    echo [3/3] 创建配置文件...
    if not exist "config.yaml" (
        if exist "config.yaml.example" (
            copy config.yaml.example config.yaml >nul
            echo ✅ 配置文件创建成功
        )
    )
    echo.
    echo ╔════════════════════════════════════════╗
    echo ║     初始化完成！正在启动哈文...         ║
    echo ╚════════════════════════════════════════╝
    echo.
)

REM 启动Flask
echo 正在启动哈文...
".venv\Scripts\python.exe" web_app.py

REM 如果Flask退出
echo.
echo 哈文已停止运行
pause
