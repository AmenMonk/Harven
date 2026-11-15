@echo off
rem detached launcher for automation (uses script's own directory)
cd /d "%~dp0"
start "HarvenAI" /min .venv\Scripts\python.exe web_app.py
