@echo off
chcp 65001 >nul
echo ========================================
echo   Harven AI - 安全重启脚本
echo ========================================
echo.

echo [1/3] 查找并停止旧进程...
netstat -ano | findstr :7000 > "%temp%\harven_port.txt" 2>nul
set found=0
for /f "tokens=5" %%a in (%temp%\harven_port.txt) do (
    set /a found+=1
    taskkill /PID %%a /F >nul 2>&1
)
del "%temp%\harven_port.txt" 2>nul
rem #1591b: flag file tells 启动HarvenAI.bat a restart is in progress (wait for port release)
echo restarting> "%~dp0data\.restarting" 2>nul

echo [2/3] 等待进程退出并确认端口释放...
rem #1591: 固定 3 秒不够（旧实例可能死得慢）→ 轮询最多约 40 秒
ping -n 2 127.0.0.1 >nul
set /a HVEN_WAIT=0
:wait_release
netstat -ano | findstr ":7000.*LISTENING" >nul 2>&1
if errorlevel 1 goto port_released
set /a HVEN_WAIT+=1
if %HVEN_WAIT% lss 15 (
    ping -n 3 127.0.0.1 >nul
    goto wait_release
)
echo [!] 警告: 端口 7000 仍被占用，请手动检查
netstat -ano | findstr :7000
del "%~dp0data\.restarting" >nul 2>&1
pause
exit /b 1

:port_released
echo [OK] 端口已释放，启动新进程...

echo.
call "%~dp0启动HarvenAI.bat"
