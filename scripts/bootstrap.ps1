# Installs Video Frame Expedition for DaVinci Resolve on a new Windows 11 PC: the missing programs
# (through winget), the application's Python packages, then its models.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -SansModeles
#
# Safe to run again: what is already there is left as it is. Neither pnpm nor just is needed:
# run.bat builds the interface with Node only (Smart App Control blocks their executables on
# some PCs). The messages on screen are in French.
#Requires -Version 5.1
param([switch]$SansModeles)
$ErrorActionPreference = "Stop"

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

if (-not (Test-Command "winget")) {
    throw "winget est introuvable : installez « App Installer » depuis le Microsoft Store, puis relancez."
}

Install-IfMissing "uv" { Test-Command "uv" } "astral-sh.uv"
Install-IfMissing "Node.js" { Test-Command "node" } "OpenJS.NodeJS.LTS"
Install-IfMissing "FFmpeg" { Test-Command "ffmpeg" } "Gyan.FFmpeg"
Install-IfMissing "ExifTool" { Test-Command "exiftool" } "OliverBetz.ExifTool"
Install-IfMissing "LM Studio" {
    Test-Path (Join-Path $env:LOCALAPPDATA "Programs\LM Studio\LM Studio.exe")
} "ElementLabs.LMStudio"

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
Write-Host "Terminé. Dans LM Studio, téléchargez un modèle de vision (par exemple qwen/qwen3-vl-8b),"
Write-Host "chargez-le et activez le serveur local, puis double-cliquez sur run.bat."
