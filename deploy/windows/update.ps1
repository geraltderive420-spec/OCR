<#
.SYNOPSIS  Met à jour l'application depuis un nouveau paquet (hors ligne) : sauvegarde, copie, dépendances, migrations, redémarrage.
.EXAMPLE   .\update.ps1 -Bundle D:\bundle-v2 -InstallDir C:\ocr-app
#>
param(
    [Parameter(Mandatory)][string]$Bundle,
    [string]$InstallDir = "C:\ocr-app",
    [int]$Port = 8443,
    [switch]$SkipBackup
)
. "$PSScriptRoot\common.ps1"
Require-Admin
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$py = Get-VenvPython $InstallDir

if (-not $SkipBackup) { Step "Sauvegarde avant mise à jour"; & "$PSScriptRoot\backup.ps1" -InstallDir $InstallDir }

Step "Arrêt des services"
Stop-Service OcrWorker, OcrApi -ErrorAction SilentlyContinue

Step "Copie du nouveau code (le .env et les données sont conservés)"
robocopy "$root\backend" "$InstallDir\backend" /E /XD data .pytest_tmp __pycache__ tests /XF .env /NFL /NDL /NJH /NJS | Out-Null
if (Test-Path "$Bundle\frontend-dist") {
    Remove-Item "$InstallDir\frontend\dist" -Recurse -Force -ErrorAction SilentlyContinue
    robocopy "$Bundle\frontend-dist" "$InstallDir\frontend\dist" /E /NFL /NDL /NJH /NJS | Out-Null
}

Step "Dépendances (roues locales)"
& $py -m pip install --no-index --find-links "$Bundle\wheels" -r "$InstallDir\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Installation des dépendances impossible" }

Step "Migrations de la base"
Push-Location "$InstallDir\backend"
try { & $py -m alembic upgrade head; if ($LASTEXITCODE -ne 0) { throw "Migrations en échec : restaurez la sauvegarde (restore.ps1)" } } finally { Pop-Location }

Step "Redémarrage"
Start-Service OcrApi, OcrWorker
& "$PSScriptRoot\test.ps1" -InstallDir $InstallDir -Port $Port
