@echo off
rem Freeze trap launcher (self-elevating) - #1581
cd /d H:\HarvenAI
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
"H:\HarvenAI\.venv\Scripts\python.exe" "H:\HarvenAI\tools\freeze_trap.py"
pause
