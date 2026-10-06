<#
.SYNOPSIS  Installe / supprime les services Windows OcrApi et OcrWorker (NSSM), avec redémarrage automatique.
.EXAMPLE   .\services.ps1 -Action install -InstallDir C:\ocr-app -Port 8443
#>
param(
    [ValidateSet("install", "remove", "restart", "status")][string]$Action = "status",
    [string]$InstallDir = "C:\ocr-app",
    [int]$Port = 8443,
    [string]$Nssm = "nssm"
)
. "$PSScriptRoot\common.ps1"
Require-Admin
$py = Get-VenvPython $InstallDir
$backend = Join-Path $InstallDir "backend"
$cert = Join-Path $InstallDir "certs"
$logs = Join-Path $InstallDir "logs"

function Install-Svc([string]$Name, [string]$SvcArgs, [string]$Display) {
    & $Nssm install $Name $py $SvcArgs | Out-Null
    & $Nssm set $Name DisplayName $Display | Out-Null
    & $Nssm set $Name AppDirectory $backend | Out-Null
    & $Nssm set $Name Start SERVICE_AUTO_START | Out-Null
    & $Nssm set $Name DependOnService MariaDB | Out-Null
    & $Nssm set $Name AppExit Default Restart | Out-Null
    & $Nssm set $Name AppRestartDelay 5000 | Out-Null
    & $Nssm set $Name AppStdout (Join-Path $logs "$Name.log") | Out-Null
    & $Nssm set $Name AppStderr (Join-Path $logs "$Name.log") | Out-Null
    & $Nssm set $Name AppRotateFiles 1 | Out-Null
    & $Nssm set $Name AppRotateBytes 10485760 | Out-Null
    # Hors ligne : aucun téléchargement à l'exécution.
    $dotenv = Read-DotEnv (Join-Path $backend ".env")
    $extra = @("HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "HF_HOME=$($dotenv['HF_HOME'])", "PYTHONUTF8=1")
    & $Nssm set $Name AppEnvironmentExtra $extra | Out-Null
}

switch ($Action) {
    "install" {
        New-Item -ItemType Directory -Force -Path $logs | Out-Null
        Step "Service OcrApi (HTTPS $Port)"
        Install-Svc "OcrApi" "-m uvicorn app.main:app --host 0.0.0.0 --port $Port --ssl-keyfile `"$cert\server.key`" --ssl-certfile `"$cert\server.crt`" --no-server-header" "OCR - API"
        Step "Service OcrWorker"
        Install-Svc "OcrWorker" "-m app.worker.runner" "OCR - Worker"
        Start-Service OcrApi, OcrWorker
        Ok "Services démarrés"
    }
    "remove"  { foreach ($n in "OcrWorker", "OcrApi") { Stop-Service $n -ErrorAction SilentlyContinue; & $Nssm remove $n confirm | Out-Null } }
    "restart" { Restart-Service OcrApi, OcrWorker }
    "status"  { Get-Service OcrApi, OcrWorker, MariaDB -ErrorAction SilentlyContinue | Format-Table Name, Status, StartType -AutoSize }
}
