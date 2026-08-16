@echo off
chcp 65001 >nul
echo ========================================
echo   Harven AI - 安全重启脚本
echo ========================================
echo.

echo [1/3] 查找并停止旧进程...
netstat -ano | findstr :5000 > "%temp%\harven_port.txt" 2>nul
set found=0
for /f "tokens=5" %%a in (%temp%\harven_port.txt) do (
    set /a found+=1
    taskkill /PID %%a /F >nul 2>&1
)
del "%temp%\harven_port.txt" 2>nul

echo [2/3] 等待进程退出...
timeout /t 3 /nobreak >nul

echo [3/3] 确认端口已释放...
netstat -ano | findstr :5000 >nul 2>&1
if %errorlevel% equ 0 (
    echo ⚠️  警告: 端口 5000 仍被占用，请手动检查
    netstat -ano | findstr :5000
    pause
) else (
    echo ✅ 端口已释放，启动新进程...
)

echo.
call "%~dp0启动HarvenAI.bat"
