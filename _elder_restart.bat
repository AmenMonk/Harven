@echo off
taskkill /F /PID 27148
timeout /t 2 /nobreak >nul
cd /d H:\HarvenAI
start "HarvenAI" /min cmd /c ".venv\Scripts\python.exe web_app.py"
exit
