@echo off
chcp 65001 >nul
title Harven AI - 安装 & 启动

echo.
echo   ╔══════════════════════════════════════════╗
echo   ║      Harven AI  — 私有本地 AI 大脑       ║
echo   ║  一键安装 ^& 启动（本地模型 + 外脑可选）     ║
echo   ╚══════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ===== Step 1: 检查 Python =====
echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [错误] 未检测到 Python！请先安装 Python 3.10+ 
    echo   下载地址：https://www.python.org/downloads/
    echo   安装时请勾选 "Add Python to PATH"
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Python %%v ✓

:: ===== Step 2: 创建虚拟环境 =====
echo.
echo [2/4] 准备 Python 虚拟环境...
if not exist ".venv\" (
    echo   正在创建虚拟环境...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo   [错误] 虚拟环境创建失败！
        pause
        exit /b 1
    )
    echo   虚拟环境已创建 ✓
) else (
    echo   虚拟环境已存在 ✓
)

:: ===== Step 3: 安装依赖 =====
echo.
echo [3/4] 安装 Python 依赖包...
call .venv\Scripts\activate.bat
pip install --quiet --upgrade pip >nul 2>&1

:: 检查是否需要安装（依赖清单对比）
set "MARKER_FILE=.venv\.harven_deps_installed"
set "NEED_INSTALL=0"

if not exist "%MARKER_FILE%" set "NEED_INSTALL=1"
if %NEED_INSTALL%==0 (
    :: 检查 requirements.txt 是否比标记文件新
    for %%F in (requirements.txt) do (
        for %%M in (%MARKER_FILE%) do (
            if "%%~tF" gtr "%%~tM" set "NEED_INSTALL=1"
        )
    )
)

if %NEED_INSTALL%==1 (
    echo   正在安装（首次可能较慢，请耐心等待）...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo   [警告] 部分依赖安装失败，尝试继续...
    )
    echo installed > "%MARKER_FILE%"
) else (
    echo   依赖已是最新 ✓
)

:: ===== Step 4: 初始化配置 =====
echo.
echo [4/4] 初始化配置文件...

:: config.yaml
if not exist "config.yaml" (
    if exist "config.yaml.example" (
        copy "config.yaml.example" "config.yaml" >nul
        echo   config.yaml 已创建（来自示例配置）✓
    ) else (
        echo   [警告] 未找到 config.yaml.example
    )
) else (
    echo   config.yaml 已存在，跳过 ✓
)

:: .env
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo   .env 已创建（来自示例配置）✓
    ) else (
        echo   [警告] 未找到 .env.example
    )
) else (
    echo   .env 已存在，跳过 ✓
)

:: 确保 data/sqlite 目录存在
if not exist "data\sqlite\" mkdir "data\sqlite"

:: ===== 检查 Ollama（可选）=====
echo.
echo ─────────────────────────────────────────
echo   检查 Ollama 服务（可选）...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   💡 未安装 Ollama — 可用外脑模式！
    echo      没有本地模型也能用：设置 → 模型管理 → 选择外脑
    echo      填 API Key 即可使用 DeepSeek/豆包等云端模型。
    echo.
    echo   📥 想用本地模型？
    echo      安装 Ollama：https://ollama.com/download/windows
    echo      然后下载模型：ollama pull qwen2.5:14b
    echo.
) else (
    echo   Ollama 已就绪 ✓ （备选本地引擎）
    echo.
)

:: ===== Ollama 国内镜像加速 =====
if "%OLLAMA_HF_MIRROR%"=="" (
    echo [加速] 已自动启用 Ollama 模型下载国内镜像 ^(modelscope.cn^)
    echo       模型下载速度提升 5-10 倍
    set "OLLAMA_HF_MIRROR=https://modelscope.cn"
)
echo.

echo ═══════════════════════════════════════════
echo   🚀 正在启动 Harven AI...
echo   地址：http://localhost:7000
echo   按 Ctrl+C 停止服务
echo ═══════════════════════════════════════════
echo.

:: 启动 Flask
python web_app.py

pause
