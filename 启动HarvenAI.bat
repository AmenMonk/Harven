@echo off
title Harven AI
rem Set console window icon to the official duck
if exist "%~dp0static\icons\duck.ico" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\set_duck_icon.ps1" "%~dp0static\icons\duck.ico" >nul 2>&1

rem Auto request admin permission (needed for file and software operations)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  Admin permission is needed to manage files and software.
    echo  Requesting administrator permission...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    timeout /t 3 /nobreak >nul
    exit /b
)

cd /d "%~dp0"

rem Check if Harven AI is installed
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  [X] Harven AI is not installed yet.
    echo  Please double-click the install bat file in this folder first.
    echo.
    pause
    exit /b 1
)

rem Check port to avoid double start
rem #1591: port busy may be a dying old instance (one-click restart) - wait up to 10s for release
netstat -ano 2>nul | findstr ":7000.*LISTENING" >nul
if errorlevel 1 goto port_free
set /a HVEN_BUSY=0
:wait_free
timeout /t 2 /nobreak >nul
netstat -ano 2>nul | findstr ":7000.*LISTENING" >nul
if errorlevel 1 goto port_free
set /a HVEN_BUSY+=2
if %HVEN_BUSY% lss 10 goto wait_free
echo  Harven AI is already running. Opening browser... window closes in 3s.
start http://localhost:7000/spa
timeout /t 3 /nobreak >nul
exit /b 0
:port_free

echo  ==========================================
echo   Harven AI is starting...
echo  ==========================================
echo.

rem Start Flask in background, poll until port 7000 is LISTENING, then open browser
rem (fix: old code slept 5s then opened browser - got ERR_CONNECTION_REFUSED because
rem  Harven needs 15-40s to fully start. Now we wait until the door is actually open.)
start /b "" .venv\Scripts\python.exe web_app.py

echo  Waiting for Harven AI to be ready - first start takes 15-40s ...
set /a HVEN_WAITED=0
:wait_ready
timeout /t 2 /nobreak >nul
netstat -ano 2>nul | findstr ":7000.*LISTENING" >nul
if not errorlevel 1 goto port_ready
set /a HVEN_WAITED+=2
if %HVEN_WAITED% lss 120 goto wait_ready
echo  [!] Port 7000 not ready after 120s. Check data\logs\runtime.log

:port_ready
start http://localhost:7000/spa

echo  Harven AI started!
echo  Browser opened at http://localhost:7000/spa
echo.
echo  Tip: closing this window will NOT stop Harven AI.
echo  To stop Harven AI: end the python process in Task Manager.
echo.
echo  Press any key to close this window - Harven AI keeps running...
pause >nul
