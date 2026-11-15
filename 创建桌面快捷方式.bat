@echo off
title Harven AI - Create Desktop Shortcut
setlocal enabledelayedexpansion
set "DP=%~dp0"
set "LAUNCH="
rem ASCII-only: find the launcher dynamically (name has Chinese chars, never hardcode it)
for %%f in ("%DP%*HarvenAI*.bat") do set "LAUNCH=%%~nxf"
if not defined LAUNCH (
    echo ERROR: HarvenAI launcher bat not found next to this script.
    pause
    exit /b 1
)
set "URLPATH=!DP:\=/!"
set "LNK=%USERPROFILE%\Desktop\Harven AI.url"
> "!LNK!" echo [InternetShortcut]
>> "!LNK!" echo URL=file:///!URLPATH!!LAUNCH!
>> "!LNK!" echo IconFile=!DP!static\icons\duck.ico
>> "!LNK!" echo IconIndex=0
echo Done - Harven AI shortcut created on desktop.
pause >nul
