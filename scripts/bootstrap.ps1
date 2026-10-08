# Installs Video Frame Expedition for DaVinci Resolve on a new Windows 11 PC: the missing programs
# (through winget), the application's Python packages, its models, then "Video Frame Expedition"
# in the Start menu.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -NoModels       (or -SansModeles)
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -WithLMStudio   (or -AvecLMStudio)
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -NoLMStudio     (or -SansLMStudio)
#
# LM Studio, which runs the vision model, is installed only when the person running the script
# wants it: the question is asked unless -WithLMStudio or -NoLMStudio answered it in advance,
# and LM Studio is left out when nobody is at the keyboard. The vision model can also come from
# the LM Studio of another computer (System page of the application).
#
# The messages are in English, or in French on a Windows set to French. VFE_LANG=fr or en (an
# environment variable, or a line of the .env file next to run.bat) decides.
#
# Safe to run again: what is already there is left as it is. Neither pnpm nor just is needed:
# run.bat builds the interface with Node only (Smart App Control blocks their executables on
# some PCs).
#Requires -Version 5.1
param(
    [Alias("SansModeles")][switch]$NoModels,
    [Alias("AvecLMStudio")][switch]$WithLMStudio,
    [Alias("SansLMStudio")][switch]$NoLMStudio
)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

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

# Passed on to the application's commands started below: one language on screen.
$env:VFE_LANG = Get-MessageLanguage

# The text in the language of the messages: the first in French, the second in English.
function T([string]$Fr, [string]$En) {
    if ($env:VFE_LANG -eq "fr") { return $Fr }
    return $En
}

if ($WithLMStudio -and $NoLMStudio) {
    throw (T "Choisissez -AvecLMStudio ou -SansLMStudio, pas les deux." "Choose -WithLMStudio or -NoLMStudio, not both.")
}

function Update-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

# $Otherwise: another way to install it, when winget fails; -OtherwiseFirst takes it first,
# without trying winget.
function Install-IfMissing([string]$Name, [scriptblock]$IsThere, [string]$WingetId, [scriptblock]$Otherwise, [switch]$OtherwiseFirst) {
    if (& $IsThere) {
        Write-Host "[ok] $Name"
        return
    }
    if ($Otherwise -and $OtherwiseFirst) {
        Write-Host (T "[..] Installation de $Name (ZIP officiel : Smart App Control refuse son installeur)" "[..] Installing $Name (official ZIP: Smart App Control refuses its installer)")
        & $Otherwise
    } else {
        Write-Host (T "[..] Installation de $Name ($WingetId)" "[..] Installing $Name ($WingetId)")
        # winget's own catalogue only: the Microsoft Store one may be missing (Store blocked in a
        # company, Windows Sandbox) and make the search fail before anything is installed.
        winget install --id $WingetId -e --source winget `
            --accept-package-agreements --accept-source-agreements --silent
        $code = $LASTEXITCODE
        if ($code -ne 0 -and $Otherwise) {
            Write-Host (T "     winget n'a pas pu l'installer (code $code) : son ZIP officiel" "     winget could not install it (code $code): its official ZIP")
            & $Otherwise
        } elseif ($code -ne 0) {
            throw (T "winget n'a pas pu installer $Name (code $code)." "winget could not install $Name (code $code).")
        }
    }
    Update-Path
    if (-not (& $IsThere)) {
        throw (T "$Name est installé, mais introuvable : relancez dans une nouvelle fenêtre." "$Name is installed, but cannot be found: run this again in a new window.")
    }
}

# Smart App Control on (1; 2 = evaluation, 0 = off): it refuses Node.js's installer (error 1723:
# the temporary DLL of its "SetInstallScope" action, nodejs/node#63005), while node.exe, signed,
# is accepted: Node.js's official ZIP is then used directly.
function Test-SmartAppControl {
    $policy = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" -ErrorAction SilentlyContinue
    return [bool]($policy -and $policy.VerifiedAndReputablePolicyState -eq 1)
}

# An official ZIP, checked against its published SHA-256 checksum: the content of its $Inner
# folder goes to %LOCALAPPDATA%\Programs\$Folder, added to the user's PATH. Returns that folder.
function Install-Zip([string]$Url, [string]$Sha256, [string]$Inner, [string]$Folder) {
    Write-Host "     $Url"
    $zip = Join-Path $env:TEMP "vfe-$Inner.zip"
    # A user agent like curl's: SourceForge then sends the file, not its page.
    Invoke-WebRequest -UseBasicParsing -UserAgent "curl/8" $Url -OutFile $zip
    if (-not $Sha256 -or (Get-FileHash $zip -Algorithm SHA256).Hash -ne $Sha256.ToUpperInvariant()) {
        Remove-Item $zip -Force
        throw (T "Le ZIP ne correspond pas à son empreinte publiée : $Url" "The ZIP does not match its published checksum: $Url")
    }
    $dest = Join-Path $env:LOCALAPPDATA "Programs\$Folder"
    # Through .NET: the module of Expand-Archive does not load everywhere (seen in Windows Sandbox).
    $unzipped = Join-Path $env:TEMP "vfe-$Inner"
    if (Test-Path $unzipped) { Remove-Item $unzipped -Recurse -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $unzipped)
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item (Join-Path $unzipped "$Inner\*") $dest -Recurse -Force
    Remove-Item $zip, $unzipped -Recurse -Force
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($user -split ";") -notcontains $dest) {
        $value = if ($user) { $user.TrimEnd(";") + ";" + $dest } else { $dest }
        [Environment]::SetEnvironmentVariable("Path", $value, "User")
    }
    return $dest
}

# Node.js 24 LTS, from nodejs.org (checksums: the version's SHASUMS256.txt).
function Install-NodeFromZip {
    # Kept in a variable first: Windows PowerShell 5.1 would otherwise pass the whole list to the
    # filter, as one block.
    $index = Invoke-RestMethod -UseBasicParsing "https://nodejs.org/dist/index.json"
    $release = $index | Where-Object { $_.lts -and $_.version -like "v24.*" } | Select-Object -First 1
    if (-not $release) { throw (T "Node.js 24 est introuvable sur nodejs.org." "Node.js 24 cannot be found on nodejs.org.") }
    $name = "node-$($release.version)-win-x64"
    $base = "https://nodejs.org/dist/$($release.version)"
    $sums = ([string](Invoke-WebRequest -UseBasicParsing "$base/SHASUMS256.txt").Content) -split "`n"
    $line = $sums | Where-Object { $_ -match "\s$([regex]::Escape($name))\.zip\s*$" } | Select-Object -First 1
    $sha = if ($line) { ($line.Trim() -split "\s+")[0] } else { "" }
    Install-Zip "$base/$name.zip" $sha $name "nodejs" | Out-Null
}

# ExifTool, from its author (exiftool.org; the ZIP is hosted by SourceForge), when winget could
# not install it. winget's installer comes first: on a PC where Smart App Control is on, it has
# the reputation that the ZIP of the same version may not have.
function Install-ExifToolFromZip {
    $version = ([string](Invoke-WebRequest -UseBasicParsing "https://exiftool.org/ver.txt").Content).Trim()
    $name = "exiftool-$($version)_64"
    $sums = ([string](Invoke-WebRequest -UseBasicParsing "https://exiftool.org/checksums.txt").Content) -split "`n"
    $line = $sums | Where-Object { $_ -like "SHA2-256($name.zip)=*" } | Select-Object -First 1
    $sha = if ($line) { ($line -split "=")[-1].Trim() } else { "" }
    $dest = Install-Zip "https://downloads.sourceforge.net/project/exiftool/$name.zip" $sha $name "ExifTool"
    # "exiftool(-k).exe" waits for a key before ending: renamed, it does not.
    Move-Item -LiteralPath (Join-Path $dest "exiftool(-k).exe") (Join-Path $dest "exiftool.exe") -Force
}

function Test-Command([string]$Command) {
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

# LM Studio is installed for the user (its own installer) or for the whole computer (winget, a
# deployment by an administrator): it is looked for in both places.
function Find-LMStudio {
    foreach ($exe in (Join-Path $env:LOCALAPPDATA "Programs\LM Studio\LM Studio.exe"),
                     (Join-Path $env:ProgramFiles "LM Studio\LM Studio.exe")) {
        if (Test-Path $exe) { return $exe }
    }
    return $null
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
    Write-Host (T "LM Studio fait tourner le modèle de vision. Il peut être installé sur ce PC, ou rester" "LM Studio runs the vision model. It can be installed on this PC, or stay on another")
    Write-Host (T "sur un autre ordinateur du réseau (son adresse se donne dans l'application, page Système)." "computer of the network (its address is given in the application, System page).")
    try {
        $answer = Read-Host (T "Installer LM Studio sur ce PC ? [o/N]" "Install LM Studio on this PC? [y/N]")
    } catch {
        return $false
    }
    return [bool]($answer -match '^\s*[oOyY]')
}

# winget comes with "App Installer", which a new Windows sometimes has not registered for this
# user yet (it comes with the first updates of the Store): registered here, otherwise
# downloaded from Microsoft.
if (-not (Test-Command "winget")) {
    Write-Host (T "[..] winget : enregistrement d'App Installer" "[..] winget: registering App Installer")
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
    } catch {
        Write-Host (T "     pas encore là : téléchargement d'App Installer (Microsoft)" "     not there yet: downloading App Installer (Microsoft)")
        try {
            $bundle = Join-Path $env:TEMP "Microsoft.DesktopAppInstaller.msixbundle"
            Invoke-WebRequest -UseBasicParsing -Uri "https://aka.ms/getwinget" -OutFile $bundle
            Add-AppxPackage -Path $bundle -ErrorAction Stop
        } catch {
            Write-Host $_.Exception.Message
        }
    }
    Update-Path
    $apps = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
    if (($env:Path -split ";") -notcontains $apps) { $env:Path += ";$apps" }
}
if (-not (Test-Command "winget")) {
    throw (T "winget est introuvable : installez « App Installer » depuis le Microsoft Store, puis relancez." "winget cannot be found: install App Installer from the Microsoft Store, then run this again.")
}

$Sac = Test-SmartAppControl
Install-IfMissing "uv" { Test-Command "uv" } "astral-sh.uv"
Install-IfMissing "Node.js" { Test-Command "node" } "OpenJS.NodeJS.LTS" { Install-NodeFromZip } -OtherwiseFirst:$Sac
Install-IfMissing "FFmpeg" { Test-Command "ffmpeg" } "Gyan.FFmpeg"
Install-IfMissing "ExifTool" { Test-Command "exiftool" } "OliverBetz.ExifTool" { Install-ExifToolFromZip }

if (Find-LMStudio) {
    Write-Host "[ok] LM Studio"
} else {
    $install = [bool]$WithLMStudio
    if (-not $WithLMStudio -and -not $NoLMStudio -and (Test-Keyboard)) {
        $install = Read-LMStudioChoice
    }
    if ($install) {
        Install-IfMissing "LM Studio" { [bool](Find-LMStudio) } "ElementLabs.LMStudio"
    } else {
        Write-Host (T "[--] LM Studio n'est pas installé sur ce PC" "[--] LM Studio is not installed on this PC")
    }
}

Set-Location $Root

Write-Host (T "[..] Paquets Python de l'application" "[..] The application's Python packages")
uv sync --locked --no-dev --inexact --project backend
if ($LASTEXITCODE -ne 0) { throw (T "uv sync a échoué (code $LASTEXITCODE)." "uv sync failed (code $LASTEXITCODE).") }

if (-not $NoModels) {
    # Offline places, sounds, speech and on-screen text, subjects, search by meaning: ~2 GB.
    foreach ($pack in "geonames", "audio-text", "subjects", "search") {
        Write-Host (T "[..] Modèles : $pack" "[..] Models: $pack")
        uv run --frozen --no-dev --project backend python -m vfe_vision models $pack
        if ($LASTEXITCODE -ne 0) { throw (T "Le téléchargement des modèles « $pack » a échoué." "Downloading the $pack models failed.") }
    }
}

# "Video Frame Expedition" in the Start menu, as in the Applications folder of a Mac: a shortcut
# to run.bat, with the application's icon, for this user only.
$Shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Video Frame Expedition.lnk"
try {
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut)
    $link.TargetPath = Join-Path $Root "run.bat"
    $link.WorkingDirectory = $Root
    $link.IconLocation = (Join-Path $Root "docs\brand\icons\app-icon.ico") + ",0"
    $link.Description = "Video Frame Expedition for DaVinci Resolve"
    $link.Save()
    Write-Host (T "[ok] « Video Frame Expedition » dans le menu Démarrer" "[ok] `"Video Frame Expedition`" in the Start menu")
} catch {
    Write-Host (T "[--] Raccourci du menu Démarrer non créé : $($_.Exception.Message)" "[--] Start menu shortcut not created: $($_.Exception.Message)")
}

Write-Host ""
if (Find-LMStudio) {
    Write-Host (T "Terminé. Dans LM Studio, téléchargez un modèle de vision (par exemple qwen/qwen3-vl-8b)," "Done. In LM Studio, download a vision model (for example qwen/qwen3-vl-8b),")
    Write-Host (T "chargez-le et activez le serveur local, puis ouvrez « Video Frame Expedition » (menu" "load it and start the local server, then open `"Video Frame Expedition`" (Start menu)")
    Write-Host (T "Démarrer) ou double-cliquez sur run.bat." "or double-click run.bat.")
} else {
    Write-Host (T "Terminé. Sur l'ordinateur qui a LM Studio, chargez un modèle de vision et laissez son" "Done. On the computer that has LM Studio, load a vision model and let its server accept")
    Write-Host (T "serveur accepter le réseau local (Developer › Server Settings › « Serve on Local Network »)." "the local network (Developer › Server Settings › `"Serve on Local Network`").")
    Write-Host (T "Ouvrez ensuite « Video Frame Expedition » (menu Démarrer) ou double-cliquez sur run.bat ;" "Then open `"Video Frame Expedition`" (Start menu) or double-click run.bat;")
    Write-Host (T "dans l'application, page Système, carte" "in the application, System page, `"LM Studio`" card:")
    Write-Host (T "« LM Studio » : choisissez « Sur un autre ordinateur » et tapez son adresse." "choose `"On another computer`" and type its address.")
    Write-Host (T "Pour installer LM Studio sur ce PC plus tard : relancez ce script avec -AvecLMStudio." "To install LM Studio on this PC later: run this script again with -WithLMStudio.")
}
