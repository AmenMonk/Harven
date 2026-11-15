@echo off
chcp 65001 >nul
echo ==========================================
echo  HarvenAI restart - kill 7000 then start
echo ==========================================
set FOUND=
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7000 ^| findstr LISTENING') do (
  set FOUND=%%a
)
if defined FOUND (
  echo Killing old PID %FOUND%
  taskkill /PID %FOUND% /F
) else (
  echo No old process on 7000
)
timeout /t 3 /nobreak >nul
netstat -ano | findstr :7000 | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
  echo PORT_STILL_BUSY - NOT starting. Close it manually then rerun.
  pause
  exit /b 1
)
echo Port free. Starting HarvenAI...
cd /d H:\HarvenAI
start "HarvenAI" "H:\HarvenAI\.venv\Scripts\python.exe" main.py
timeout /t 6 /nobreak >nul
netstat -ano | findstr :7000 | findstr LISTENING
if errorlevel 1 (
  echo START_CHECK_FAIL - port 7000 not listening
) else (
  echo START_OK - HarvenAI is listening on 7000
)
timeout /t 8 /nobreak >nul
