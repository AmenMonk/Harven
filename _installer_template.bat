@echo off
chcp 65001 >nul 2>&1
title Harven AI 首次安装
cd /d "%~dp0"

echo ╔════════════════════════════════════════╗
echo ║     Harven AI 首次安装                 ║
echo ║     正在创建桌面快捷方式...            ║
echo ╚════════════════════════════════════════╝
echo.

REM 创建桌面快捷方式
set "desktop=%USERPROFILE%\Desktop"
set "shortcut=%desktop%\Harven AI.lnk"
set "target=%~dp0启动哈文.bat"
set "icon=%~dp0static\icons\duck.ico"

REM 用PowerShell创建快捷方式
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%shortcut%'); $sc.TargetPath = '%target%'; $sc.WorkingDirectory = '%~dp0'; $sc.IconLocation = '%icon%'; $sc.Description = 'Harven AI 哈文 - 私有AI助手'; $sc.Save()"

if %errorlevel%==0 (
    echo ✅ 桌面快捷方式创建成功！
) else (
    echo ❌ 快捷方式创建失败，请手动创建
)

echo.
echo ╔════════════════════════════════════════╗
echo ║           安装完成！                   ║
echo ║                                        ║
echo ║   桌面已创建"Harven AI"快捷方式       ║
echo ║   双击即可启动哈文                     ║
echo ╚════════════════════════════════════════╝
echo.
pause
