<#
.SYNOPSIS  Test de bon fonctionnement : auto-diagnostic de l'application, services Windows, réponse HTTPS.
#>
param(
    [string]$InstallDir = "C:\ocr-app",
    [int]$Port = 8443
)
. "$PSScriptRoot\common.ps1"
$py = Get-VenvPython $InstallDir
$failed = 0

Step "Services Windows"
foreach ($n in "MariaDB", "OcrApi", "OcrWorker") {
    $s = Get-Service $n -ErrorAction SilentlyContinue
    if ($s -and $s.Status -eq "Running") { Ok $n } else { Ko "$n n'est pas démarré"; $failed++ }
}

Step "Auto-diagnostic de l'application"
Push-Location (Join-Path $InstallDir "backend")
try { & $py -m app.cli check; if ($LASTEXITCODE -ne 0) { $failed++ } } finally { Pop-Location }

Step "Réponse HTTPS locale"
try {
    # Certificat interne auto-signé : on vérifie seulement que le service répond sur le port prévu.
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    $r = Invoke-WebRequest -Uri "https://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 10
    if ($r.StatusCode -eq 200) { Ok "https://127.0.0.1:$Port/api/health" } else { Ko "statut $($r.StatusCode)"; $failed++ }
} catch { Ko "Pas de réponse sur le port $Port"; $failed++ }

if ($failed -gt 0) { Write-Host "`n$failed contrôle(s) en échec." -ForegroundColor Red; exit 1 }
Write-Host "`nTous les contrôles sont réussis." -ForegroundColor Green
