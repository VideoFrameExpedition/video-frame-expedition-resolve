# Updates Video Frame Expedition for DaVinci Resolve in its folder: the latest version, then
# what it needs (scripts\bootstrap.ps1 -Update), then offers to start it in the same window.
# Started by update.bat (double-click) or "Video Frame Expedition - update" in the Start menu.
#
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -From <ZIP or folder>
#
# What is kept: the data folder (%LOCALAPPDATA%\vfe-vision: the library's database with the
# preferences, the models, the media, the logs, the access token), the .env file, the
# application's environment and the interface's dependencies. The database is also backed up
# by the application itself before its structure changes, at the first start of the new version.
#
# A folder that comes from Git is updated with git pull. Otherwise the latest release is
# downloaded from GitHub and replaces the application's files; what the new version no longer
# has goes from the application's own folders (backend, frontend, scripts, docs), and nothing
# else of the folder is touched.
#
# -From installs that version instead (a ZIP downloaded from GitHub, or a folder): to try one
# before it is published. -NoInstall replaces the files only.
#Requires -Version 5.1
param(
    [Alias("Depuis")][string]$From,
    [Alias("SansInstallation")][switch]$NoInstall,
    [int]$Port = 8765
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue" # the progress bar slows a download down tenfold
$Root = Split-Path $PSScriptRoot -Parent
$Repository = "VideoFrameExpedition/video-frame-expedition-resolve"

function Get-MessageLanguage {
    $lang = $env:VFE_LANG
    $envFile = Join-Path $Root ".env"
    if (-not $lang -and (Test-Path $envFile)) {
        $line = Get-Content $envFile -Encoding UTF8 |
            Where-Object { $_ -match '^\s*VFE_LANG\s*=' } | Select-Object -Last 1
        if ($line) { $lang = ($line -split '=', 2)[1].Trim().Trim('"', "'") }
    }
    if (-not $lang) { $lang = (Get-UICulture).TwoLetterISOLanguageName }
    if ($lang -like "fr*") { return "fr" }
    return "en"
}

# Passed on to the scripts and the application started below: one language on screen.
$env:VFE_LANG = Get-MessageLanguage

# The text in the language of the messages: the first in French, the second in English.
function T([string]$Fr, [string]$En) {
    if ($env:VFE_LANG -eq "fr") { return $Fr }
    return $En
}

function Test-Keyboard {
    # Someone can answer a question: an interactive session whose input is not redirected.
    if (-not [Environment]::UserInteractive) { return $false }
    if ([Environment]::GetCommandLineArgs() | Where-Object { $_ -like "-NonI*" }) { return $false }
    try {
        return -not [Console]::IsInputRedirected
    } catch {
        return $false
    }
}

# The version of the application in a folder, from backend\pyproject.toml.
function Get-Version([string]$Folder) {
    $project = Join-Path $Folder "backend\pyproject.toml"
    if (-not (Test-Path $project)) { return $null }
    foreach ($line in Get-Content $project -Encoding UTF8) {
        if ($line -match '^version\s*=\s*"([^"]+)"') { return $Matches[1] }
    }
    return $null
}

function Test-Newer([string]$Version, [string]$Than) {
    try {
        return [version]$Version -gt [version]$Than
    } catch {
        return $Version -ne $Than
    }
}

# The latest release, from the address GitHub sends to: no API, so no limit on requests.
function Get-LatestTag {
    $request = [System.Net.WebRequest]::Create("https://github.com/$Repository/releases/latest")
    $request.Method = "HEAD"
    $request.AllowAutoRedirect = $false
    $request.UserAgent = "video-frame-expedition-update"
    $response = $request.GetResponse()
    try {
        $location = $response.Headers["Location"]
    } finally {
        $response.Close()
    }
    if ($location -notmatch '/releases/tag/([^/?#]+)$') {
        throw (T "Impossible de connaître la dernière version sur GitHub (réponse : $location)." "Cannot tell the latest version on GitHub (answer: $location).")
    }
    return [Uri]::UnescapeDataString($Matches[1])
}

# A version of the application, as a folder: a folder given as it is, or a ZIP unzipped in the
# temporary folder (GitHub puts everything in one folder named after the version).
function Get-VersionFolder([string]$Source, [string]$Unzipped) {
    if (Test-Path $Source -PathType Leaf) {
        if (Test-Path $Unzipped) { Remove-Item $Unzipped -Recurse -Force }
        # Through .NET: the module of Expand-Archive does not load everywhere (seen in Windows Sandbox).
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Source, $Unzipped)
        $Source = $Unzipped
    }
    foreach ($folder in @($Source) + @(Get-ChildItem $Source -Directory | ForEach-Object { $_.FullName })) {
        if ((Get-Version $folder) -and (Test-Path (Join-Path $folder "run.bat"))) {
            return (Resolve-Path $folder).Path
        }
    }
    throw (T "$Source ne contient pas l'application (ni backend\pyproject.toml ni run.bat)." "$Source does not hold the application (no backend\pyproject.toml nor run.bat).")
}

# Never removed, wherever they are: what is written next to the application's files (its
# environment, the interface's dependencies and build, Python's caches) and hidden files (.env).
$Kept = '^(\..*|node_modules|dist|__pycache__|.*\.egg-info)$'

# The files of a folder that belong to the application, without going into those kept.
function Get-OwnFiles([string]$Folder) {
    foreach ($item in Get-ChildItem -LiteralPath $Folder -Force) {
        if ($item.Name -match $Kept) { continue }
        if ($item.PSIsContainer) { Get-OwnFiles $item.FullName } else { $item.FullName }
    }
}

# The new version's files replace the application's: in the application's own folders, those it
# no longer has are removed first. A file of the user's at the top of the folder stays.
function Copy-Version([string]$New, [string]$Target) {
    $files = @{}
    foreach ($file in Get-ChildItem -LiteralPath $New -Recurse -File -Force) {
        $files[$file.FullName.Substring($New.Length).TrimStart("\")] = $file.FullName
    }
    $removed = 0
    foreach ($folder in Get-ChildItem -LiteralPath $New -Directory -Force) {
        $mine = Join-Path $Target $folder.Name
        if ($folder.Name -match $Kept -or -not (Test-Path $mine)) { continue }
        foreach ($path in Get-OwnFiles $mine) {
            if (-not $files.ContainsKey($path.Substring($Target.Length).TrimStart("\"))) {
                Remove-Item -LiteralPath $path -Force
                $removed++
            }
        }
    }
    foreach ($relative in $files.Keys) {
        $destination = Join-Path $Target $relative
        $parent = Split-Path $destination -Parent
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force $parent | Out-Null }
        Copy-Item -LiteralPath $files[$relative] -Destination $destination -Force
    }
    Write-Host (T "[ok] Fichiers de l'application : $($files.Count) remplacés ; retirés : $removed" "[ok] Files of the application: $($files.Count) replaced; removed: $removed")
}

function Get-LockHash {
    $lock = Join-Path $Root "frontend\pnpm-lock.yaml"
    if (Test-Path $lock) { return (Get-FileHash $lock -Algorithm SHA256).Hash }
    return ""
}

# --- The application must be stopped: its files are in use while it runs. --------------------
while (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host (T "L'application tourne : fermez sa fenêtre (ou appuyez sur Ctrl+C dans celle-ci)." "The application is running: close its window (or press Ctrl+C in it).")
    if (-not (Test-Keyboard)) { exit 1 }
    $answer = Read-Host (T "Appuyez sur Entrée une fois l'application fermée (n : abandonner)" "Press Return once the application is closed (n: give up)")
    if ($answer -like "n*") { exit 1 }
}

$Installed = Get-Version $Root
Write-Host (T "Video Frame Expedition $Installed, dans $Root" "Video Frame Expedition $Installed, in $Root")
$LockBefore = Get-LockHash
$Changed = $false

if (-not $From -and (Test-Path (Join-Path $Root ".git"))) {
    # A folder that comes from Git: git knows what changed.
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw (T "Ce dossier vient de Git, mais git est introuvable : mettez-le à jour avec git pull." "This folder comes from Git, but git cannot be found: update it with git pull.")
    }
    Write-Host (T "[..] Recherche d'une nouvelle version (git fetch)" "[..] Looking for a new version (git fetch)")
    git -C $Root fetch --quiet
    if ($LASTEXITCODE -ne 0) { throw (T "git fetch a échoué (voir le message de git ci-dessus)." "git fetch failed (see git's message above).") }
    $behind = git -C $Root rev-list --count "HEAD..@{upstream}"
    if ($LASTEXITCODE -ne 0) { throw (T "Cette branche ne suit aucune branche de GitHub : mettez-la à jour avec git pull." "This branch follows no branch of GitHub: update it with git pull.") }
    if ([int]$behind -gt 0) {
        git -C $Root pull --ff-only
        if ($LASTEXITCODE -ne 0) { throw (T "git pull a échoué (voir le message de git ci-dessus)." "git pull failed (see git's message above).") }
        $Changed = $true
    }
} else {
    $temporary = Join-Path $env:TEMP "vfe-update"
    New-Item -ItemType Directory -Force $temporary | Out-Null
    $source = $null
    if ($From) {
        $source = (Resolve-Path $From).Path
    } else {
        Write-Host (T "[..] Recherche d'une nouvelle version sur GitHub" "[..] Looking for a new version on GitHub")
        $tag = Get-LatestTag
        $latest = $tag.TrimStart("v")
        if (-not $Installed -or (Test-Newer $latest $Installed)) {
            Write-Host (T "[..] Téléchargement de la version $latest" "[..] Downloading version $latest")
            $source = Join-Path $temporary "$tag.zip"
            Invoke-WebRequest -UseBasicParsing "https://github.com/$Repository/archive/refs/tags/$tag.zip" -OutFile $source
        }
    }
    if ($source) {
        $new = Get-VersionFolder $source (Join-Path $temporary "unzipped")
        # A ZIP from the Internet marks its files: Windows would ask before each script.
        Get-ChildItem -LiteralPath $new -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
        Write-Host (T "[..] Version $(Get-Version $new) : remplacement des fichiers de l'application" "[..] Version $(Get-Version $new): replacing the application's files")
        Copy-Version $new $Root
        $Changed = $true
    }
    Remove-Item $temporary -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not $Changed) {
    Write-Host (T "[ok] Vous avez déjà la dernière version ($Installed)." "[ok] You already have the latest version ($Installed).")
    exit 0
}

# New dependencies for the interface: installed again by run.bat, at the next start.
if ((Get-LockHash) -ne $LockBefore -and (Test-Path (Join-Path $Root "frontend\node_modules"))) {
    Write-Host (T "[..] Les dépendances de l'interface ont changé : run.bat les réinstallera" "[..] The interface's dependencies changed: run.bat will install them again")
    cmd /c rmdir /s /q (Join-Path $Root "frontend\node_modules")
}

if ($NoInstall) { exit 0 }

# The new version's installation (the new bootstrap.ps1, in its own process): its packages,
# its models, its shortcuts; what is already there is left as it is.
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\bootstrap.ps1") -Update
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host (T "[ok] Video Frame Expedition $(Get-Version $Root) est installé." "[ok] Video Frame Expedition $(Get-Version $Root) is installed.")

if ((Test-Keyboard) -and -not $env:VFE_NO_PAUSE) {
    $answer = Read-Host (T "Démarrer l'application maintenant, dans cette fenêtre ? [O/n]" "Start the application now, in this window? [Y/n]")
    if ($answer -notlike "n*") {
        & (Join-Path $Root "run.bat")
        exit $LASTEXITCODE
    }
}
Write-Host (T "Pour la démarrer : « Video Frame Expedition » dans le menu Démarrer, ou run.bat." "To start it: open Video Frame Expedition from the Start menu, or run.bat.")
