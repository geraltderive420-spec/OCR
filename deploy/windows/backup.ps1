<#
.SYNOPSIS  Sauvegarde chiffrée (AES-256, 7-Zip) de la base MariaDB et des fichiers de données.
.DESCRIPTION
  Mot de passe de sauvegarde : variable OCR_BACKUP_PASSWORD ou saisie masquée. À conserver HORS du poste (coffre).
  Les sauvegardes de plus de -KeepDays jours sont supprimées.
#>
param(
    [string]$InstallDir = "C:\ocr-app",
    [string]$Destination = "E:\sauvegardes_ocr",
    [int]$KeepDays = 30,
    [string]$SevenZip = "C:\Program Files\7-Zip\7z.exe",
    [string]$MysqlDump = ""
)
. "$PSScriptRoot\common.ps1"
$envf = Read-DotEnv (Join-Path $InstallDir "backend\.env")
if (-not $envf["OCR_DATABASE_URL"]) { throw "OCR_DATABASE_URL absent de .env" }
if ($envf["OCR_DATABASE_URL"] -notmatch '^mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d*)/([^?]+)') { throw "URL de base non reconnue" }
$dbUser, $dbPwd, $dbHost, $dbPort, $db = $Matches[1], $Matches[2], $Matches[3], $Matches[4], $Matches[5]
if (-not $dbPort) { $dbPort = "3306" }
if (-not $MysqlDump) {
    $MysqlDump = (Get-ChildItem "C:\Program Files\MariaDB*\bin\mysqldump.exe" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
    if (-not $MysqlDump) { $MysqlDump = (Get-Command mysqldump -ErrorAction SilentlyContinue).Source }
}
if (-not $MysqlDump -or -not (Test-Path $SevenZip)) { throw "mysqldump ou 7-Zip introuvable (voir -MysqlDump / -SevenZip)." }
$backupPwd = $env:OCR_BACKUP_PASSWORD
if (-not $backupPwd) { $backupPwd = Read-SecretText "Mot de passe de chiffrement de la sauvegarde" }

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$tmp = Join-Path $env:TEMP "ocr-backup-$stamp"
New-Item -ItemType Directory -Force -Path $tmp, $Destination | Out-Null
try {
    Step "Export de la base"
    $env:MYSQL_PWD = $dbPwd   # évite le mot de passe sur la ligne de commande
    & $MysqlDump --host=$dbHost --port=$dbPort --user=$dbUser --single-transaction --routines --default-character-set=utf8mb4 --result-file="$tmp\base.sql" $db
    if ($LASTEXITCODE -ne 0) { throw "mysqldump a échoué" }

    Step "Archive chiffrée"
    $data = $envf["OCR_DATA_DIR"]; if (-not $data) { $data = Join-Path $InstallDir "backend\data" }
    $out = Join-Path $Destination "ocr-$stamp.7z"
    & $SevenZip a -t7z -mhe=on "-p$backupPwd" -mx=3 $out "$tmp\base.sql" (Join-Path $data "uploads") (Join-Path $data "exports") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7-Zip a échoué" }
    Ok "Sauvegarde : $out"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
}
Get-ChildItem $Destination -Filter "ocr-*.7z" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } | Remove-Item -Force
