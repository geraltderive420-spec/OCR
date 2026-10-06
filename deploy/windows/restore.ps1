<#
.SYNOPSIS  Restaure une sauvegarde créée par backup.ps1 (base + fichiers). Les services sont arrêtés puis relancés.
.EXAMPLE   .\restore.ps1 -Archive E:\sauvegardes_ocr\ocr-20261007-020000.7z
#>
param(
    [Parameter(Mandatory)][string]$Archive,
    [string]$InstallDir = "C:\ocr-app",
    [string]$SevenZip = "C:\Program Files\7-Zip\7z.exe",
    [string]$Mysql = ""
)
. "$PSScriptRoot\common.ps1"
Require-Admin
$envf = Read-DotEnv (Join-Path $InstallDir "backend\.env")
if ($envf["OCR_DATABASE_URL"] -notmatch '^mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d*)/([^?]+)') { throw "URL de base non reconnue" }
$dbUser, $dbPwd, $dbHost, $dbPort, $db = $Matches[1], $Matches[2], $Matches[3], $Matches[4], $Matches[5]
if (-not $dbPort) { $dbPort = "3306" }
if (-not $Mysql) { $Mysql = (Get-ChildItem "C:\Program Files\MariaDB*\bin\mysql.exe" | Select-Object -First 1).FullName }
$backupPwd = Read-SecretText "Mot de passe de la sauvegarde"
$tmp = Join-Path $env:TEMP "ocr-restore-$(Get-Date -Format yyyyMMddHHmmss)"
Stop-Service OcrWorker, OcrApi -ErrorAction SilentlyContinue
try {
    Step "Extraction"
    & $SevenZip x "-p$backupPwd" "-o$tmp" -y $Archive | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Extraction impossible (mot de passe ?)" }
    Step "Base de données"
    $env:MYSQL_PWD = $dbPwd
    Get-Content "$tmp\base.sql" -Raw -Encoding UTF8 | & $Mysql --host=$dbHost --port=$dbPort --user=$dbUser --default-character-set=utf8mb4 $db
    Step "Fichiers"
    $data = $envf["OCR_DATA_DIR"]; if (-not $data) { $data = Join-Path $InstallDir "backend\data" }
    foreach ($d in "uploads", "exports") { if (Test-Path "$tmp\$d") { Copy-Item "$tmp\$d" $data -Recurse -Force } }
    Ok "Restauration terminée"
} finally {
    Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
Start-Service OcrApi, OcrWorker
