@echo off
chcp 65001 >nul 2>nul
title Harven AI - 安装联网搜索（SearXNG）

echo.
echo   ╔══════════════════════════════════════╗
echo   ║   Harven AI  联网搜索安装器          ║
echo   ║   一键安装 SearXNG 搜索引擎           ║
echo   ╚══════════════════════════════════════╝
echo.

:: ====== Step 1: 检查 Docker ======
echo [1/3] 检查 Docker...
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ⚠️  你的电脑还没装 Docker
    echo.
    echo Docker 是一个容器运行环境，SearXNG 需要它
    echo 安装只需 3 步（全程自动）：
    echo.
    echo ── 请选择 ──
    echo [1] 自动下载安装 Docker（推荐）
    echo [2] 手动打开下载页面
    echo [3] 我装过了，再检测一次
    echo.
    choice /c 123 /n /m "请选择 [1/2/3]: "
    if !errorlevel! equ 1 (
        echo.
        echo 正在下载 Docker Desktop（约 600MB，请耐心等待）...
        powershell -Command ^
          "Invoke-WebRequest -Uri 'https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe' -OutFile '%TEMP%\DockerDesktopInstaller.exe'"
        if exist "%TEMP%\DockerDesktopInstaller.exe" (
            echo 正在安装 Docker（请在弹出窗口点 OK，安装完会提示重启）...
            start /wait "" "%TEMP%\DockerDesktopInstaller.exe" install --quiet --accept-license
            del "%TEMP%\DockerDesktopInstaller.exe" 2>nul
            echo.
            echo ⚠️  Docker 安装完成！请重启电脑后再次运行本脚本
            echo    重启后双击桌面上的 Docker Desktop 图标等它变绿
            pause
            exit /b 0
        ) else (
            echo ❌ 下载失败，请手动下载
            start https://www.docker.com/products/docker-desktop/
            pause
            exit /b 1
        )
    )
    if !errorlevel! equ 2 (
        start https://www.docker.com/products/docker-desktop/
        echo 下载安装后请重启电脑，再运行本脚本
        pause
        exit /b 0
    )
    if !errorlevel! equ 3 (
        echo 请确保 Docker Desktop 已启动（右下角鲸鱼图标变绿）
        pause
        exit /b 1
    )
)

echo ✅ Docker 已安装
echo.

:: ====== Step 2: 检查 Docker 是否在运行 ======
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ⚠️  Docker 已安装但未启动
    echo.
    echo 请手动操作：
    echo   1. 在开始菜单找到 "Docker Desktop"
    echo   2. 双击打开
    echo   3. 等右下角鲸鱼图标 🐳 变绿
    echo   4. 再回来运行本脚本
    echo.
    pause
    exit /b 0
)
echo ✅ Docker 正在运行
echo.

:: ====== Step 3: 安装 SearXNG ======
echo [2/3] 检查 SearXNG...
curl -s -o nul -w "%%{http_code}" http://localhost:8888/ 2>nul | find "200" >nul
if %errorlevel% equ 0 (
    echo ✅ SearXNG 已在运行！
    goto :done
)

echo 正在下载安装 SearXNG（约 200MB，首次较慢）...
docker pull docker.io/searxng/searxng:latest >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️ DockerHub 拉取较慢，切换镜像...
    docker pull ghcr.io/searxng/searxng:latest >nul 2>&1
)

:: 如果之前有旧容器，先删掉
docker rm -f searxng >nul 2>nul

echo 正在启动 SearXNG...
docker run --name searxng -d -p 8888:8080 docker.io/searxng/searxng:latest >nul 2>&1
if %errorlevel% neq 0 (
    docker run --name searxng -d -p 8888:8080 ghcr.io/searxng/searxng:latest >nul 2>&1
)

:: 等待启动
echo 等待 SearXNG 启动...
set /a c=0
:waitSearxng
set /a c+=1
timeout /t 2 /nobreak >nul
curl -s -o nul -w "%%{http_code}" http://localhost:8888/ 2>nul | find "200" >nul
if %errorlevel% equ 0 goto :done
if !c! lss 15 goto :waitSearxng
echo ❌ SearXNG 启动超时
pause
exit /b 1

:done
echo.
echo [3/3] 配置哈文...

:: 写入哈文配置
cd /d "%~dp0"
if not exist "startup_config.json" (
    echo {"searxng_url":"http://localhost:8888","web_search_enabled":true} > startup_config.json
)

echo.
echo ╔══════════════════════════════════════════╗
echo ║  ✅ 安装完成！                           ║
echo ║                                          ║
echo ║  SearXNG: http://localhost:8888          ║
echo ║  哈文AI:  http://127.0.0.1:7000          ║
echo ║                                          ║
echo ║  💡 哈文设置里点「检测连接」确认          ║
echo ║  💡 输入框右边点「🌐 联网开」即可联网搜索 ║
echo ╚══════════════════════════════════════════╝
echo.
echo 按任意键打开哈文...
pause >nul
start http://127.0.0.1:7000
exit /b 0
