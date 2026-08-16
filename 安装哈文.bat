@echo off
chcp 65001 >nul
title 🦆 Harven AI 哈文 — 安装向导
mode con: cols=70 lines=20

echo.
echo   ╔══════════════════════════════════════════╗
echo   ║          🦆 Harven AI 哈文               ║
echo   ║        一键安装 + 启动 + 桌面图标          ║
echo   ║                                          ║
echo   ║  双击此文件即可完成全部安装，无需其他操作    ║
echo   ╚══════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ===== Step 1: 检查 Python =====
echo  [1/5] 检查 Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ❌ 未检测到 Python！
    echo.
    echo  请先安装 Python 3.12+：
    echo  1. 打开 https://www.python.org/downloads/
    echo  2. 下载并安装
    echo  3. 安装时务必勾选 "Add Python to PATH" ← 重要！
    echo  4. 安装完成后重新双击此文件
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo  ✓ Python %%v 检测到

:: ===== Step 2: 创建虚拟环境 =====
echo.
echo  [2/5] 创建虚拟环境...
if not exist ".venv\" (
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo  ❌ 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo  ✓ 虚拟环境已创建
) else (
    echo  ✓ 虚拟环境已存在
)

:: ===== Step 3: 安装依赖 =====
echo.
echo  [3/5] 安装依赖包...
call .venv\Scripts\activate.bat
pip install --quiet --upgrade pip >nul 2>&1

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
    echo  正在安装（首次约3-5分钟，请耐心等待）...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo  ⚠️ 部分依赖安装失败，尝试继续...
    )
    echo installed > "%MARKER_FILE%"
    echo  ✓ 依赖安装完成
) else (
    echo  ✓ 依赖已是最新
)

:: ===== Step 4: 初始化配置 =====
echo.
echo  [4/5] 初始化配置...
if not exist "config.yaml" (
    if exist "config.example.yaml" (
        copy "config.example.yaml" "config.yaml" >nul
        echo  ✓ config.yaml 已创建
    ) else if exist "config.yaml.example" (
        copy "config.yaml.example" "config.yaml" >nul
        echo  ✓ config.yaml 已创建
    )
) else (
    echo  ✓ config.yaml 已存在
)
if not exist "data\sqlite\" mkdir "data\sqlite"
echo  ✓ 数据目录就绪

:: ===== Step 5: 创建桌面快捷方式（带鸭子图标）=====
echo.
echo  [5/5] 创建桌面快捷方式...

:: 找鸭子图标
set "DUCK_ICON="
if exist "static\icons\duck.ico" set "DUCK_ICON=static\icons\duck.ico"
if not defined DUCK_ICON if exist "static\icon-duck.png" set "DUCK_ICON=static\icon-duck.png"
if not defined DUCK_ICON if exist "static\icons\duck_192.png" set "DUCK_ICON=static\icons\duck_192.png"

:: 用PowerShell创建快捷方式（带图标）
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$desktop = [Environment]::GetFolderPath('Desktop');" ^
  "$sc = $ws.CreateShortcut(\"$desktop\Harven AI.lnk\");" ^
  "$sc.TargetPath = '%~dp0启动HarvenAI.bat';" ^
  "$sc.WorkingDirectory = '%~dp0';" ^
  "$sc.WindowStyle = 7;" ^
  "$sc.Description = 'Harven AI 哈文 — 本地AI助手';" ^
  "if ('%DUCK_ICON%' -ne '') { $sc.IconLocation = '%~dp0%DUCK_ICON%, 0' };" ^
  "$sc.Save();" ^
  "Write-Output 'OK'"

if %errorlevel% == 0 (
    echo  ✓ 桌面快捷方式已创建（🦆鸭子图标）
) else (
    echo  ⚠️ 快捷方式创建失败，请手动双击 启动HarvenAI.bat
)

:: ===== 完成 =====
echo.
echo  ═══════════════════════════════════════════
echo.
echo  ✅ 安装完成！
echo.
echo  🦆 桌面已出现"Harven AI"鸭子图标
echo     双击它即可启动哈文
echo.
echo  🌐 启动后浏览器自动打开 http://localhost:7000
echo.
echo  📝 首次使用：
echo     1. 初始管理员密码：harven2026（首次登录强制修改）
echo     2. 设置 → 模型管理 → 填API Key（DeepSeek/GLM等）
echo     3. 开始对话！
echo.
echo  ═══════════════════════════════════════════
echo.
echo  现在启动哈文？（按任意键启动，或关闭窗口稍后启动）
echo.
pause

:: ===== 启动 =====
start "" "启动HarvenAI.bat"
exit
