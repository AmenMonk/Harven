@echo off
title Harven AI - One-Click Upgrade
rem ASCII-only wrapper: real logic (with Chinese text) lives in upgrade.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0upgrade.ps1" %*
echo.
pause
exit /b 0
