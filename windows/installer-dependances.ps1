<#
  Installe ce qu'il faut pour MIDI vers MP3 : FFmpeg, FluidSynth et une soundfont.
  - Aucun droit administrateur requis (tout va dans votre profil utilisateur).
  - Peut être relancé sans risque : ce qui est déjà installé est ignoré.
  Lancement conseillé : double-clic sur installer-dependances.bat
#>
param([switch]$NoPause)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # l'affichage de progression ralentit beaucoup les téléchargements
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Has($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }

function Add-UserPath($dir) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($current -split ";") -notcontains $dir) {
        [Environment]::SetEnvironmentVariable("Path", ($current.TrimEnd(";") + ";" + $dir), "User")
        Say "   dossier ajouté au PATH utilisateur : $dir"
    }
}

# Prévient l'Explorateur et les nouveaux programmes que le PATH a changé (évite de redémarrer la session)
function Broadcast-EnvironmentChange {
    try {
        Add-Type -Namespace Win32 -Name Native -MemberDefinition '[DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Auto)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);' -ErrorAction SilentlyContinue
        $r = [UIntPtr]::Zero
        [Win32.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$r) | Out-Null
    } catch { }
}

$problems = @()
Refresh-Path
Say "`nInstallation des dépendances de MIDI vers MP3`n" Cyan

# --- 1. FFmpeg -------------------------------------------------------------
Say "1/3  FFmpeg" Cyan
if (Has ffmpeg) {
    Say "   déjà installé, rien à faire." Green
} elseif (Has winget) {
    try {
        winget install --id Gyan.FFmpeg -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
        Refresh-Path
        if (Has ffmpeg) { Say "   installé." Green } else { Say "   installé (visible après réouverture du terminal)." Green }
    } catch { $problems += "FFmpeg : l'installation par winget a échoué ($_)." }
} else {
    $problems += "FFmpeg : winget est introuvable. Téléchargez FFmpeg sur https://www.gyan.dev/ffmpeg/builds/ et ajoutez son dossier bin au PATH."
}

# --- 2. FluidSynth ---------------------------------------------------------
Say "2/3  FluidSynth" Cyan
if (Has fluidsynth) {
    Say "   déjà installé, rien à faire." Green
} else {
    try {
        $api = Invoke-RestMethod "https://api.github.com/repos/FluidSynth/fluidsynth/releases/latest" -Headers @{ "User-Agent" = "midi-to-mp3-installer" }
        $asset = $api.assets | Where-Object { $_.name -like "*win10-x64*" } | Select-Object -First 1
        if (-not $asset) { throw "aucune archive Windows 64 bits dans la dernière version ($($api.tag_name))" }
        $zip = Join-Path $env:TEMP $asset.name
        Say "   téléchargement de $($asset.name)…"
        Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
        $dest = Join-Path $env:LOCALAPPDATA "fluidsynth"
        New-Item -ItemType Directory -Force $dest | Out-Null
        Expand-Archive $zip -DestinationPath $dest -Force
        Remove-Item $zip -ErrorAction SilentlyContinue
        $exe = Get-ChildItem $dest -Recurse -Filter fluidsynth.exe | Select-Object -First 1
        if (-not $exe) { throw "fluidsynth.exe introuvable après extraction" }
        Add-UserPath $exe.DirectoryName
        Refresh-Path
        Say "   installé dans $($exe.DirectoryName)." Green
    } catch { $problems += "FluidSynth : $_ . Téléchargez-le sur https://github.com/FluidSynth/fluidsynth/releases et ajoutez son dossier bin au PATH." }
}

# --- 3. Soundfont ----------------------------------------------------------
Say "3/3  Soundfont (les sons des instruments)" Cyan
$sfDir = Join-Path $env:USERPROFILE "soundfonts"
New-Item -ItemType Directory -Force $sfDir | Out-Null
$existing = @(Get-ChildItem (Join-Path $sfDir "*") -Include *.sf2, *.sf3 -File -ErrorAction SilentlyContinue)
if ($existing) {
    Say "   déjà présente : $($existing[0].Name)" Green
} else {
    try {
        $target = Join-Path $sfDir "GeneralUser-GS.sf2"
        Say "   téléchargement de GeneralUser GS (environ 31 Mo)…"
        Invoke-WebRequest "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2" -OutFile "$target.part" -UseBasicParsing
        Move-Item "$target.part" $target -Force
        Say "   enregistrée dans $sfDir." Green
    } catch {
        Remove-Item "$sfDir\GeneralUser-GS.sf2.part" -ErrorAction SilentlyContinue
        $problems += "Soundfont : le téléchargement a échoué ($_). Déposez vous-même un fichier .sf2 dans $sfDir."
    }
}

Broadcast-EnvironmentChange

Say ""
if ($problems.Count -eq 0) {
    Say "Tout est prêt. Vous pouvez lancer MIDI-vers-MP3.exe." Green
    Say "(Si une fenêtre de terminal était déjà ouverte, fermez-la et rouvrez-la pour qu'elle voie les nouveaux outils.)"
} else {
    Say "Terminé, mais $($problems.Count) point(s) à régler :" Yellow
    $problems | ForEach-Object { Say " - $_" Yellow }
}

if (-not $NoPause -and $Host.Name -eq "ConsoleHost") { Read-Host "`nAppuyez sur Entrée pour fermer" | Out-Null }
