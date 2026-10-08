@echo off
rem Video Frame Expedition for DaVinci Resolve - Windows update (double-click): the latest
rem version in this folder (scripts\update.ps1), keeping your library, your preferences, the
rem models and the .env file; then it offers to start the application in this same window.
rem The Start menu has it too: "Video Frame Expedition - update". Its options are passed on:
rem -From <ZIP or folder> installs that version instead (or -Depuis).
setlocal EnableExtensions
chcp 65001 >nul
title Video Frame Expedition for DaVinci Resolve
cd /d "%~dp0"

rem Started from PowerShell 7, the folders of its modules would be passed on to Windows
rem PowerShell, which then misses some of its own commands (Get-FileHash): emptied, it
rem rebuilds its own.
set "PSModulePath="
rem The update replaces this file too, and cmd.exe reads a batch file line by line as it goes:
rem what follows is one block, read in one go before the update starts. Its exit code goes
rem through delayed expansion (!CODE!): %errorlevel% would be read with the block, and
rem "exit /b 1" inside a block ends the batch file with the code 0.
setlocal EnableDelayedExpansion
(
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\update.ps1" %*
    set "CODE=!errorlevel!"
    if not defined VFE_NO_PAUSE pause
    exit /b !CODE!
)
