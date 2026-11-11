<#
  Construit l'exécutable Windows et le zip à publier.
  Usage :  powershell -ExecutionPolicy Bypass -File .\build_windows.ps1
  Résultat : dist\MIDI-vers-MP3.exe  et  dist\MIDI-vers-MP3-windows.zip (+ .sha256)
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Run($what, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "Échec : $what (code $LASTEXITCODE)" }
}

Write-Host "1/4  Dépendances de construction" -ForegroundColor Cyan
Run "pip install" { python -m pip install --quiet -r requirements.txt -r requirements-dev.txt }

Write-Host "2/4  Tests" -ForegroundColor Cyan
Run "tests" { python -m unittest }

Write-Host "3/4  Exécutable (PyInstaller)" -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Run "PyInstaller" {
    python -m PyInstaller --noconfirm --clean --onefile --console `
        --name "MIDI-vers-MP3" `
        --add-data "$PSScriptRoot\index.html;." `
        --specpath build `
        app.py
}

Write-Host "4/4  Archive zip" -ForegroundColor Cyan
$stage = "dist\zip"
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item "dist\MIDI-vers-MP3.exe" $stage
Copy-Item "windows\installer-dependances.ps1" $stage
Copy-Item "windows\installer-dependances.bat" $stage
Copy-Item "windows\LISEZMOI.txt" $stage
Copy-Item "LICENSE" $stage
Copy-Item "THIRD_PARTY_NOTICES.md" $stage
$zip = "dist\MIDI-vers-MP3-windows.zip"
Compress-Archive -Path "$stage\*" -DestinationPath $zip -Force
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
"$hash  MIDI-vers-MP3-windows.zip" | Set-Content "$zip.sha256" -Encoding ascii

$exeMb = [math]::Round((Get-Item "dist\MIDI-vers-MP3.exe").Length / 1MB, 1)
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "`nTerminé : exe $exeMb Mo, zip $zipMb Mo" -ForegroundColor Green
Write-Host "SHA256  : $hash"
