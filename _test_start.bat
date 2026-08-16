@echo off
cd /d H:\HarvenAI
echo %date% %time% Starting... > start_log2.txt
C:\Users\usera\.workbuddy\binaries\python\envs\default\Scripts\python.exe -u web_app.py >> start_log2.txt 2>&1
echo %date% %time% EXIT: %errorlevel% >> start_log2.txt
