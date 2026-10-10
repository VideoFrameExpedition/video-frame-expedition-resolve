@echo off
rem Video Frame Expedition for DaVinci Resolve - Windows launcher (double-click).
rem   run.bat            starts the application and opens the browser
rem   run.bat build      first rebuilds the web interface, even when its sources have not
rem                      changed (after an update, it is rebuilt anyway)
rem   run.bat tailscale  also listens on this computer's Tailscale address (your other
rem                      devices, token required); or VFE_TAILSCALE=true in the .env file
rem The messages are in English, or in French on a Windows set to French; VFE_LANG=fr or en in
rem the .env file decides.
setlocal EnableExtensions
chcp 65001 >nul
title Video Frame Expedition for DaVinci Resolve
cd /d "%~dp0"
set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%"
set "PYTHONUTF8=1"

rem --- Language of the messages: VFE_LANG (variable or .env file), otherwise Windows' one -----
rem (Passed on to the application: the window never mixes two languages.)
if not defined VFE_LANG if exist ".env" for /f "usebackq eol=# tokens=1,* delims== " %%A in (".env") do if /i "%%A"=="VFE_LANG" set "VFE_LANG=%%B"
if not defined VFE_LANG for /f %%L in ('powershell -NoProfile -Command "(Get-UICulture).TwoLetterISOLanguageName"') do set "VFE_LANG=%%L"
if defined VFE_LANG set "VFE_LANG=%VFE_LANG:"=%"
if /i "%VFE_LANG:~0,2%"=="fr" (set "VFE_LANG=fr") else (set "VFE_LANG=en")

rem --- Already running? Just open the interface. ---------------------------------------------
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
    echo.
    call :say " L'application tourne déjà : ouverture de %URL%" " The application is already running: opening %URL%"
    call :say " Pour la voir dans une fenêtre (journal, Ctrl+C pour arrêter), fermez d'abord" " To see it in a window (log, Ctrl+C to stop), first close the instance that is"
    call :say " l'instance en cours, puis relancez run.bat." " running, then start run.bat again."
    echo.
    if not defined VFE_NO_BROWSER start "" "%URL%"
    if not defined VFE_NO_BROWSER timeout /t 15
    exit /b 0
)

rem --- Required tools (PATH, otherwise their usual installation folders) ----------------------
call :find_tools
where uv >nul 2>nul
if errorlevel 1 call :offer_install
where uv >nul 2>nul
if errorlevel 1 (
    call :say "[ERREUR] « uv » est introuvable : double-cliquez sur install.bat." "[ERROR] uv cannot be found: double-click install.bat."
    pause
    exit /b 1
)
where ffmpeg >nul 2>nul || call :say "[ATTENTION] ffmpeg est introuvable : l'analyse des vidéos échouera." "[WARNING] ffmpeg cannot be found: the analysis of the videos will fail."
where exiftool >nul 2>nul || call :say "[ATTENTION] ExifTool est introuvable : pas de métadonnées ni de date de tournage." "[WARNING] ExifTool cannot be found: no metadata nor shooting date."

rem --- Options: "build" (interface rebuilt), "tailscale" (access from your devices) -----------
set "BUILD="
for %%A in (%*) do (
    if /i "%%~A"=="build" set "BUILD=1"
    if /i "%%~A"=="tailscale" set "VFE_TAILSCALE=true"
)

rem --- Web interface: built when missing, when its sources changed since (an update), or on
rem request (run.bat build) --------------------------------------------------------------------
if not exist "backend\src\vfe_vision\web\dist\index.html" set "BUILD=1"
if not defined BUILD (
    uv run --project backend python scripts\copy_frontend_build.py --up-to-date || set "BUILD=1"
)
if defined BUILD call :build_ui || goto :failed

echo.
echo  Video Frame Expedition for DaVinci Resolve  -  %URL%
call :say " Pensez à lancer LM Studio avec le modèle de vision chargé sur le GPU." " Remember to start LM Studio with the vision model loaded on the GPU."
call :say " Assistants (Claude, Cursor...) et accès depuis vos autres appareils : page « Connexions »." " Assistants (Claude, Cursor...) and access from your other devices: Connections page."
call :say " Pour arrêter l'application : fermez cette fenêtre ou appuyez sur Ctrl+C." " To stop the application: close this window or press Ctrl+C."
echo.

rem --- Opens the browser as soon as the server answers (in the background) --------------------
if not defined VFE_NO_BROWSER (
    start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 240; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%URL%/api/v1/system/health' | Out-Null; Start-Process '%URL%'; exit } catch { Start-Sleep -Milliseconds 500 } }"
)

rem Python tools go through "python -m": Smart App Control blocks the .exe files of a venv.
rem --frozen: the versions of uv.lock, which is never rewritten; --no-dev: no development tools.
rem Code 75: the interface asked for a restart (an import or a reset of the data): again.
rem VFE_LAUNCHER tells the server so: started any other way, nothing would start it again.
set "VFE_LAUNCHER=run.bat"
:serve
uv run --frozen --no-dev --project backend python -m vfe_vision serve --port %PORT%
if errorlevel 76 goto :failed
if errorlevel 75 goto :serve
if errorlevel 1 goto :failed
exit /b 0

:say
rem Shows the first text in French, the second in English (VFE_LANG, set above).
rem Outside any block in parentheses: a text may contain some.
if not "%VFE_LANG%"=="fr" shift
echo(%~1
exit /b 0

:offer_install
rem Not installed yet: install.bat is offered, then the application starts.
call :say "L'application n'est pas encore installée sur ce PC (« uv » est introuvable)." "The application is not installed on this PC yet (uv cannot be found)."
if "%VFE_LANG%"=="fr" (set "ASK=L'installer maintenant ? [O/n] ") else (set "ASK=Install it now? [Y/n] ")
rem Return alone keeps the default answer (yes): the variable is never empty.
set "REPONSE=o"
set /p "REPONSE=%ASK%"
if /i "%REPONSE:~0,1%"=="n" exit /b 0
set "VFE_NO_PAUSE=1"
call "%~dp0install.bat"
set "VFE_NO_PAUSE="
call :find_tools
exit /b 0

:find_tools
rem A window opened before an installation (winget, npm) does not have the updated PATH.
rem (Each addition goes through :add_path: inside a loop, %PATH% would be read only once.)
for %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Links" "%USERPROFILE%\.local\bin" "%ProgramFiles%\nodejs" "%LOCALAPPDATA%\Programs\nodejs" "%APPDATA%\npm" "%LOCALAPPDATA%\Programs\ExifTool" "%LOCALAPPDATA%\Programs\ffmpeg\bin") do (
    if exist "%%~D\" call :add_path "%%~D"
)
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*") do call :add_path "%%~D"
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*") do (
    for /d %%E in ("%%~D\ffmpeg-*") do call :add_path "%%~E\bin"
)
exit /b 0

:add_path
set "PATH=%PATH%;%~1"
exit /b 0

:build_ui
rem The build uses Node only: pnpm is an executable since its version 11, and Smart App Control
rem blocks it. pnpm only installs the dependencies, the first time (:install_ui).
where node >nul 2>nul
if errorlevel 1 (
    call :say "[ERREUR] Node.js est introuvable : impossible de construire l'interface web." "[ERROR] Node.js cannot be found: the web interface cannot be built."
    call :say "         Pour l'installer : double-cliquez sur install.bat." "        To install it: double-click install.bat."
    exit /b 1
)
if not exist "frontend\node_modules\vite\bin\vite.js" call :install_ui || exit /b 1
call :say "Construction de l'interface web..." "Building the web interface..."
pushd frontend
node node_modules\typescript\bin\tsc -b && node node_modules\vite\bin\vite.js build
set "BUILT=%errorlevel%"
popd
if not "%BUILT%"=="0" exit /b 1
uv run --frozen --no-dev --project backend python scripts\copy_frontend_build.py || exit /b 1
exit /b 0

:install_ui
rem An installed pnpm is tried first. Otherwise (a new PC), pnpm runs through Node (npx), in the
rem version pinned by frontend\package.json: its lockfile, written as two documents, can only be
rem read by pnpm 12 (pnpm 10 refuses it).
call :say "Installation des dépendances de l'interface web..." "Installing the web interface's dependencies..."
where pnpm.cmd >nul 2>nul
if not errorlevel 1 (
    call pnpm.cmd --dir frontend install --frozen-lockfile
    if not errorlevel 1 exit /b 0
    call :say "[ATTENTION] pnpm a échoué (Windows l'a peut-être bloqué) : nouvel essai par Node." "[WARNING] pnpm failed (Windows may have blocked it): trying again through Node."
)
where npx.cmd >nul 2>nul
if errorlevel 1 (
    call :say "[ERREUR] Ni pnpm ni npx : impossible d'installer les dépendances de l'interface." "[ERROR] Neither pnpm nor npx: the interface's dependencies cannot be installed."
    call :say "         Pour installer Node.js : double-cliquez sur install.bat." "        To install Node.js: double-click install.bat."
    exit /b 1
)
call npx.cmd --yes pnpm@12.6.0 --dir frontend install --frozen-lockfile
if errorlevel 1 (
    call :say "[ERREUR] Les dépendances de l'interface web n'ont pas pu être installées." "[ERROR] The web interface's dependencies could not be installed."
    exit /b 1
)
exit /b 0

:failed
echo.
call :say "[ERREUR] L'application s'est arrêtée sur une erreur (voir les messages ci-dessus)." "[ERROR] The application stopped on an error (see the messages above)."
pause
exit /b 1
