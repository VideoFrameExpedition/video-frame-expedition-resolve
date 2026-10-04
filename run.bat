@echo off
rem Video Frame Expedition for DaVinci Resolve - Windows launcher (double-click).
rem   run.bat            starts the application and opens the browser
rem   run.bat build      first rebuilds the web interface (after an update)
rem   run.bat tailscale  also listens on this computer's Tailscale address (your other
rem                      devices, token required); or VFE_TAILSCALE=true in the .env file
setlocal EnableExtensions
chcp 65001 >nul
title Video Frame Expedition for DaVinci Resolve
cd /d "%~dp0"
set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%"
set "PYTHONUTF8=1"

rem --- Already running? Just open the interface. ---------------------------------------------
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
    echo.
    echo  L'application tourne déjà : ouverture de %URL%
    echo  Pour la voir dans une fenêtre ^(journal, Ctrl+C pour arrêter^), fermez d'abord
    echo  l'instance en cours, puis relancez run.bat.
    echo.
    if not defined VFE_NO_BROWSER start "" "%URL%"
    if not defined VFE_NO_BROWSER timeout /t 15
    exit /b 0
)

rem --- Required tools (PATH, otherwise their usual installation folders) ----------------------
call :find_tools
where uv >nul 2>nul
if errorlevel 1 (
    echo [ERREUR] « uv » est introuvable. Installez-le avec : winget install astral-sh.uv
    pause
    exit /b 1
)
where ffmpeg >nul 2>nul || echo [ATTENTION] ffmpeg est introuvable : l'analyse des vidéos échouera.
where exiftool >nul 2>nul || echo [ATTENTION] ExifTool est introuvable : pas de métadonnées ni de date de tournage.

rem --- Options: "build" (interface rebuilt), "tailscale" (access from your devices) -----------
set "BUILD="
for %%A in (%*) do (
    if /i "%%~A"=="build" set "BUILD=1"
    if /i "%%~A"=="tailscale" set "VFE_TAILSCALE=true"
)

rem --- Web interface: built when missing, or on request (run.bat build) -----------------------
if not exist "backend\src\vfe_vision\web\dist\index.html" set "BUILD=1"
if defined BUILD call :build_ui || goto :failed

echo.
echo  Video Frame Expedition for DaVinci Resolve  -  %URL%
echo  Pensez à lancer LM Studio avec le modèle de vision chargé sur le GPU.
echo  Assistants (Claude, Cursor...) et accès depuis vos autres appareils : page « Connexions ».
echo  Pour arrêter l'application : fermez cette fenêtre ou appuyez sur Ctrl+C.
echo.

rem --- Opens the browser as soon as the server answers (in the background) --------------------
if not defined VFE_NO_BROWSER (
    start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 240; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%URL%/api/v1/system/health' | Out-Null; Start-Process '%URL%'; exit } catch { Start-Sleep -Milliseconds 500 } }"
)

rem Python tools go through "python -m": Smart App Control blocks the .exe files of a venv.
rem --frozen: the versions of uv.lock, which is never rewritten; --no-dev: no development tools.
uv run --frozen --no-dev --project backend python -m vfe_vision serve --port %PORT%
if errorlevel 1 goto :failed
exit /b 0

:find_tools
rem A window opened before an installation (winget, npm) does not have the updated PATH.
rem (Each addition goes through :add_path: inside a loop, %PATH% would be read only once.)
for %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Links" "%USERPROFILE%\.local\bin" "%ProgramFiles%\nodejs" "%APPDATA%\npm" "%LOCALAPPDATA%\Programs\ExifTool" "%LOCALAPPDATA%\Programs\ffmpeg\bin") do (
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
    echo [ERREUR] Node.js est introuvable : impossible de construire l'interface web.
    echo          Installez-le avec : winget install OpenJS.NodeJS.LTS
    exit /b 1
)
if not exist "frontend\node_modules\vite\bin\vite.js" call :install_ui || exit /b 1
echo Construction de l'interface web...
pushd frontend
node node_modules\typescript\bin\tsc -b && node node_modules\vite\bin\vite.js build
set "BUILT=%errorlevel%"
popd
if not "%BUILT%"=="0" exit /b 1
uv run --frozen --no-dev --project backend python scripts\copy_frontend_build.py || exit /b 1
exit /b 0

:install_ui
rem pnpm is an executable since its version 11, which Smart App Control blocks on some PCs. Its
rem version 10, written in JavaScript, runs through Node (npx) and reads the same lockfile; an
rem exact version, so that every PC runs the same installer.
echo Installation des dépendances de l'interface web...
where pnpm.cmd >nul 2>nul
if not errorlevel 1 (
    call pnpm.cmd --dir frontend install --frozen-lockfile
    if not errorlevel 1 exit /b 0
    echo [ATTENTION] pnpm a échoué ^(Windows l'a peut-être bloqué^) : essai avec pnpm 10, par Node.
)
where npx.cmd >nul 2>nul
if errorlevel 1 (
    echo [ERREUR] Ni pnpm ni npx : impossible d'installer les dépendances de l'interface.
    echo          Installez Node.js avec : winget install OpenJS.NodeJS.LTS
    exit /b 1
)
rem Without these two settings, pnpm 10 would fetch the version pinned in package.json.
set "npm_config_manage_package_manager_versions=false"
set "npm_config_package_manager_strict=false"
call npx.cmd --yes pnpm@10.34.6 --dir frontend install --frozen-lockfile
if errorlevel 1 (
    echo [ERREUR] Les dépendances de l'interface web n'ont pas pu être installées.
    exit /b 1
)
exit /b 0

:failed
echo.
echo [ERREUR] L'application s'est arrêtée sur une erreur (voir les messages ci-dessus).
pause
exit /b 1
