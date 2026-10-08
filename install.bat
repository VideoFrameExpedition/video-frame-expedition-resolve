@echo off
rem Video Frame Expedition for DaVinci Resolve - Windows installer (double-click), for the
rem application's folder downloaded as a ZIP or with git clone: installs what is missing
rem (scripts\bootstrap.ps1: winget, uv, Node.js, FFmpeg, ExifTool, the Python packages and the
rem models; LM Studio when wanted), then offers to start the application in this same window.
rem Its options are passed on: -NoModels, -WithLMStudio, -NoLMStudio (or -SansModeles,
rem -AvecLMStudio, -SansLMStudio).
setlocal EnableExtensions
chcp 65001 >nul
title Video Frame Expedition for DaVinci Resolve
cd /d "%~dp0"

rem --- Language of the messages: as run.bat (VFE_LANG, the .env file, else Windows's) -------
if not defined VFE_LANG if exist ".env" for /f "usebackq eol=# tokens=1,* delims== " %%A in (".env") do if /i "%%A"=="VFE_LANG" set "VFE_LANG=%%B"
if not defined VFE_LANG for /f %%L in ('powershell -NoProfile -Command "(Get-UICulture).TwoLetterISOLanguageName"') do set "VFE_LANG=%%L"
if defined VFE_LANG set "VFE_LANG=%VFE_LANG:"=%"
if /i "%VFE_LANG:~0,2%"=="fr" (set "VFE_LANG=fr") else (set "VFE_LANG=en")

rem Started from PowerShell 7, the folders of its modules would be passed on to Windows
rem PowerShell, which then misses some of its own commands (Get-FileHash): emptied, it
rem rebuilds its own.
set "PSModulePath="
rem A folder downloaded as a ZIP carries the mark of the Web on every file: removed here, so
rem that Windows asks nothing more for run.bat and the scripts.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\bootstrap.ps1" %*
set "CODE=%errorlevel%"
rem Started by run.bat, which starts the application itself next: nothing more.
if defined VFE_NO_PAUSE exit /b %CODE%
echo.
if not "%CODE%"=="0" goto :end

rem The application starts in this same window if wanted: run.bat takes over.
if "%VFE_LANG%"=="fr" (set "ASK=Démarrer l'application maintenant, dans cette fenêtre ? [O/n] ") else (set "ASK=Start the application now, in this window? [Y/n] ")
rem Return alone keeps the default answer (yes): the variable is never empty.
set "REPONSE=o"
set /p "REPONSE=%ASK%"
if /i not "%REPONSE:~0,1%"=="n" "%~dp0run.bat"
call :say "Pour la démarrer plus tard : « Video Frame Expedition » dans le menu Démarrer, ou run.bat." "To start it later: open Video Frame Expedition from the Start menu, or run.bat."

:end
pause
exit /b %CODE%

:say
rem Shows the first text in French, the second in English (VFE_LANG, set above).
rem Outside any block in parentheses: a text may contain some.
if not "%VFE_LANG%"=="fr" shift
echo(%~1
exit /b 0
