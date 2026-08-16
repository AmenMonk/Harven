@echo off
:: Harven 哈文AI — 桌面快捷方式创建脚本
:: 双击此文件即可在桌面创建快捷方式

set SHORTCUT_NAME=Harven哈文AI
set TARGET=H:\HarvenAI\启动HarvenAI.bat
set WORKDIR=H:\HarvenAI

:: 使用 PowerShell 创建
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\%SHORTCUT_NAME%.lnk'); $sc.TargetPath = '%TARGET%'; $sc.WorkingDirectory = '%WORKDIR%'; $sc.Save()"
echo ✅ 桌面快捷方式 "Harven哈文AI" 已创建
pause