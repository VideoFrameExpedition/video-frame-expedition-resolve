# Installs Video Frame Expedition for DaVinci Resolve on a new Windows 11 PC: the missing programs
# (through winget), the application's Python packages, then its models.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -SansModeles
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -AvecLMStudio
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -SansLMStudio
#
# LM Studio, which runs the vision model, is installed only when the person running the script
# wants it: the question is asked unless -AvecLMStudio or -SansLMStudio answered it in advance,
# and LM Studio is left out when nobody is at the keyboard. The vision model can also come from
# the LM Studio of another computer (System page of the application).
#
# Safe to run again: what is already there is left as it is. Neither pnpm nor just is needed:
# run.bat builds the interface with Node only (Smart App Control blocks their executables on
# some PCs). The messages on screen are in French.
#Requires -Version 5.1
param([switch]$SansModeles, [switch]$AvecLMStudio, [switch]$SansLMStudio)
$ErrorActionPreference = "Stop"

if ($AvecLMStudio -and $SansLMStudio) {
    throw "Choisissez -AvecLMStudio ou -SansLMStudio, pas les deux."
}

function Update-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

function Install-IfMissing([string]$Name, [scriptblock]$IsThere, [string]$WingetId) {
    if (& $IsThere) {
        Write-Host "[ok] $Name"
        return
    }
    Write-Host "[..] Installation de $Name ($WingetId)"
    # winget's own catalogue only: the Microsoft Store one may be missing (Store blocked in a
    # company, Windows Sandbox) and make the search fail before anything is installed.
    winget install --id $WingetId -e --source winget `
        --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget n'a pas pu installer $Name (code $LASTEXITCODE)."
    }
    Update-Path
}

function Test-Command([string]$Command) {
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
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

function Read-LMStudioChoice {
    Write-Host ""
    Write-Host "LM Studio fait tourner le modèle de vision. Il peut être installé sur ce PC, ou rester"
    Write-Host "sur un autre ordinateur du réseau (son adresse se donne dans l'application, page Système)."
    try {
        $answer = Read-Host "Installer LM Studio sur ce PC ? [o/N]"
    } catch {
        return $false
    }
    return [bool]($answer -match '^\s*[oOyY]')
}

if (-not (Test-Command "winget")) {
    throw "winget est introuvable : installez « App Installer » depuis le Microsoft Store, puis relancez."
}

Install-IfMissing "uv" { Test-Command "uv" } "astral-sh.uv"
Install-IfMissing "Node.js" { Test-Command "node" } "OpenJS.NodeJS.LTS"
Install-IfMissing "FFmpeg" { Test-Command "ffmpeg" } "Gyan.FFmpeg"
Install-IfMissing "ExifTool" { Test-Command "exiftool" } "OliverBetz.ExifTool"

$LMStudioExe = Join-Path $env:LOCALAPPDATA "Programs\LM Studio\LM Studio.exe"
if (Test-Path $LMStudioExe) {
    Write-Host "[ok] LM Studio"
} else {
    $install = [bool]$AvecLMStudio
    if (-not $AvecLMStudio -and -not $SansLMStudio -and (Test-Keyboard)) {
        $install = Read-LMStudioChoice
    }
    if ($install) {
        Install-IfMissing "LM Studio" { Test-Path $LMStudioExe } "ElementLabs.LMStudio"
    } else {
        Write-Host "[--] LM Studio n'est pas installé sur ce PC"
    }
}

Set-Location (Split-Path $PSScriptRoot -Parent)

# Exactly the versions of uv.lock (--locked), without the development tools (--no-dev);
# --inexact leaves alone what a developer installed on top.
Write-Host "[..] Paquets Python de l'application"
uv sync --locked --no-dev --inexact --project backend
if ($LASTEXITCODE -ne 0) { throw "uv sync a échoué (code $LASTEXITCODE)." }

if (-not $SansModeles) {
    # Offline places, sounds, speech and on-screen text, subjects, search by meaning: ~2 GB.
    foreach ($pack in "geonames", "audio-text", "subjects", "search") {
        Write-Host "[..] Modèles : $pack"
        uv run --frozen --no-dev --project backend python -m vfe_vision models $pack
        if ($LASTEXITCODE -ne 0) { throw "Le téléchargement des modèles « $pack » a échoué." }
    }
}

Write-Host ""
if (Test-Path $LMStudioExe) {
    Write-Host "Terminé. Dans LM Studio, téléchargez un modèle de vision (par exemple qwen/qwen3-vl-8b),"
    Write-Host "chargez-le et activez le serveur local, puis double-cliquez sur run.bat."
} else {
    Write-Host "Terminé. Sur l'ordinateur qui a LM Studio, chargez un modèle de vision et laissez son"
    Write-Host "serveur accepter le réseau local (Developer › Server Settings › « Serve on Local Network »)."
    Write-Host "Double-cliquez ensuite sur run.bat ; dans l'application, page Système, carte"
    Write-Host "« LM Studio » : choisissez « Sur un autre ordinateur » et tapez son adresse."
    Write-Host "Pour installer LM Studio sur ce PC plus tard : relancez ce script avec -AvecLMStudio."
}
