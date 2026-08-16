@echo off
:: Harven AI 后台启动脚本（开机自启用，无 pause）
cd /d "H:\HarvenAI"

:: 等待网络就绪（可选）
timeout /t 5 /nobreak >nul

:: 启动 Flask 服务
start /B ".venv\Scripts\python.exe" web_app.py > harven_startup.log 2>&1

exit
