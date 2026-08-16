@echo off
chcp 65001 >nul
title 🦆 Harven AI 哈文

:: ★ 2026-08-14: 自动请求管理员权限（删文件/装软件需要）
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  需要管理员权限来操作文件和软件
    echo  正在请求管理员权限...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

:: 检查有没有 .venv
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  ❌ 哈文未安装！
    echo.
    echo  请双击 "安装哈文.bat" 完成安装
    echo.
    pause
    exit /b 1
)

:: 检查端口，避免重复启动
netstat -ano 2>nul | findstr ":7000.*LISTENING" >nul
if not errorlevel 1 (
    echo  🦆 哈文已在运行，正在打开浏览器...
    start http://localhost:7000
    exit /b 0
)

:: 启动
echo  ═══════════════════════════════════════════
echo  🦆 Harven AI 哈文正在启动...
echo  ═══════════════════════════════════════════
echo.
echo  🔐 初始密码：harven2026（首次登录后建议修改）
echo.

:: 后台启动Flask + 等待3秒 + 自动打开浏览器
start /b .venv\Scripts\python.exe web_app.py

:: 等待Flask启动
timeout /t 5 /nobreak >nul

:: 自动打开浏览器
start http://localhost:7000

echo  ✅ 哈文已启动！
echo  🌐 浏览器已自动打开 http://localhost:7000
echo.
echo  💡 关闭此窗口不会停止哈文
echo    要停止：任务管理器 → 结束 python.exe
echo.
echo  按任意键关闭此窗口（哈文继续运行）...
pause >nul
