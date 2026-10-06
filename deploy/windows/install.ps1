<#
.SYNOPSIS
  Installation complète sur le poste de production, HORS LIGNE (câble réseau débranché possible).
.DESCRIPTION
  Prérequis installés au préalable depuis le paquet : Python 3.11-3.13, MariaDB (service "MariaDB"), pilote NVIDIA,
  redistribuables VC++, Ollama (+ modèles copiés), NSSM, 7-Zip, et llama-server.exe compilé pour la carte (arch 86).
  Ce script : copie l'application, crée l'environnement Python depuis les roues locales, crée la base et son
  utilisateur dédié, écrit .env (secrets aléatoires), applique les migrations, crée le premier administrateur,
  génère le certificat HTTPS interne, installe les services et le pare-feu, puis lance le test de bon fonctionnement.
.EXAMPLE
  .\install.ps1 -Bundle D:\bundle -InstallDir C:\ocr-app -DataDir D:\ocr_data -Hostname ocr.mairie.local -Ip 192.168.1.20
#>
param(
    [Parameter(Mandatory)][string]$Bundle,
    [string]$InstallDir = "C:\ocr-app",
    [string]$DataDir = "D:\ocr_data",
    [Parameter(Mandatory)][string]$Hostname,
    [string[]]$Ip = @(),
    [string]$Python = "python",
    [string]$LlamaDir = "C:\LLAMA.CPP.CUDA\llama.cpp\build\bin\Release",
    [int]$Port = 8443
)
. "$PSScriptRoot\common.ps1"
Require-Admin
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

Step "Vérification des prérequis"
$ver = & $Python --version 2>&1
if ($ver -notmatch "Python 3\.(11|12|13)") { throw "Python 3.11 à 3.13 requis (trouvé : $ver)" }
if (-not (Get-Service MariaDB -ErrorAction SilentlyContinue)) { throw "Le service MariaDB est introuvable." }
if (-not (Test-Path (Join-Path $LlamaDir "llama-server.exe"))) { throw "llama-server.exe introuvable dans $LlamaDir" }
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { Write-Warning "nvidia-smi introuvable : pilote NVIDIA absent ?" }
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) { throw "NSSM introuvable dans le PATH." }
foreach ($p in "$Bundle\wheels", "$Bundle\hf") { if (-not (Test-Path $p)) { throw "Paquet incomplet : $p manquant" } }
Ok "Prérequis"

Step "Copie de l'application vers $InstallDir"
New-Item -ItemType Directory -Force -Path "$InstallDir\backend", "$InstallDir\certs", "$InstallDir\logs", $DataDir | Out-Null
robocopy "$root\backend" "$InstallDir\backend" /E /XD data .pytest_tmp __pycache__ tests /XF .env /NFL /NDL /NJH /NJS | Out-Null
if (Test-Path "$Bundle\frontend-dist") { robocopy "$Bundle\frontend-dist" "$InstallDir\frontend\dist" /E /NFL /NDL /NJH /NJS | Out-Null }
elseif (Test-Path "$root\frontend\dist") { robocopy "$root\frontend\dist" "$InstallDir\frontend\dist" /E /NFL /NDL /NJH /NJS | Out-Null }
else { throw "Interface compilée introuvable (frontend\dist)." }
Ok "Fichiers copiés"

Step "Environnement Python (installation depuis les roues locales, sans Internet)"
$py = Get-VenvPython $InstallDir
if (-not (Test-Path $py)) { & $Python -m venv "$InstallDir\venv" }
& $py -m pip install --no-index --find-links "$Bundle\wheels" torch torchvision
& $py -m pip install --no-index --find-links "$Bundle\wheels" -r "$InstallDir\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Installation des dépendances impossible (roues manquantes dans $Bundle\wheels ?)" }
Ok "Dépendances installées"

Step "Modèles Surya 2 et poids de détection (cache local)"
New-Item -ItemType Directory -Force -Path "$DataDir\hf" | Out-Null
robocopy "$Bundle\hf" "$DataDir\hf" /E /NFL /NDL /NJH /NJS | Out-Null
if (Test-Path "$Bundle\datalab") { robocopy "$Bundle\datalab" (Join-Path $env:LOCALAPPDATA "datalab") /E /NFL /NDL /NJH /NJS | Out-Null }
Ok "Modèles copiés"

Step "Base de données et utilisateur dédié"
$rootPwd = Read-SecretText "Mot de passe root de MariaDB"
$appPwd = New-RandomSecret 24
$secret = New-RandomSecret 48
$env:OCR_DB_ROOT_PWD = $rootPwd; $env:OCR_DB_APP_PWD = $appPwd
$sql = @"
import os, pymysql
c = pymysql.connect(host='127.0.0.1', user='root', password=os.environ['OCR_DB_ROOT_PWD'], charset='utf8mb4')
cur = c.cursor()
cur.execute("CREATE DATABASE IF NOT EXISTS ocr CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
cur.execute("CREATE USER IF NOT EXISTS 'ocr_app'@'localhost' IDENTIFIED BY %s", (os.environ['OCR_DB_APP_PWD'],))
cur.execute("ALTER USER 'ocr_app'@'localhost' IDENTIFIED BY %s", (os.environ['OCR_DB_APP_PWD'],))
cur.execute("GRANT ALL PRIVILEGES ON ocr.* TO 'ocr_app'@'localhost'")
c.commit()
"@
$sql | & $py -
if ($LASTEXITCODE -ne 0) { throw "Création de la base impossible (mot de passe root ?)" }
Remove-Item Env:\OCR_DB_ROOT_PWD, Env:\OCR_DB_APP_PWD

Step "Fichier .env (droits restreints)"
$envPath = "$InstallDir\backend\.env"
$example = Get-Content "$InstallDir\backend\.env.example" -Raw -Encoding UTF8
$example = $example -replace "(?m)^OCR_DATABASE_URL=.*$", "OCR_DATABASE_URL=mysql+pymysql://ocr_app:$appPwd@127.0.0.1:3306/ocr?charset=utf8mb4"
$example = $example -replace "(?m)^OCR_SECRET_KEY=.*$", "OCR_SECRET_KEY=$secret"
$example = $example -replace "(?m)^OCR_DATA_DIR=.*$", "OCR_DATA_DIR=$($DataDir -replace '\\','/')"
$example = $example -replace "(?m)^OCR_LLAMA_CPP_DIR=.*$", "OCR_LLAMA_CPP_DIR=$($LlamaDir -replace '\\','/')"
$example = $example -replace "(?m)^HF_HOME=.*$", "HF_HOME=$(($DataDir + '\hf') -replace '\\','/')"
Set-Content -Path $envPath -Value $example -Encoding UTF8
icacls $envPath /inheritance:r /grant:r "SYSTEM:(R)" "Administrators:(F)" | Out-Null
Ok ".env écrit"

Step "Migrations"
Push-Location "$InstallDir\backend"
try { & $py -m alembic upgrade head; if ($LASTEXITCODE -ne 0) { throw "Migrations en échec" } } finally { Pop-Location }

Step "Premier administrateur"
$adminName = Read-Host "Identifiant de l'administrateur"
$env:OCR_ADMIN_PASSWORD = Read-SecretText "Mot de passe provisoire (10 caractères minimum ; changement imposé à la 1re connexion)"
Push-Location "$InstallDir\backend"
try { & $py -m app.cli create-admin $adminName } finally { Pop-Location; Remove-Item Env:\OCR_ADMIN_PASSWORD }

Step "Certificat HTTPS interne"
Push-Location "$InstallDir\certs"
try { & $py "$PSScriptRoot\make_cert.py" $Hostname @Ip } finally { Pop-Location }
icacls "$InstallDir\certs\server.key" /inheritance:r /grant:r "SYSTEM:(R)" "Administrators:(F)" | Out-Null

Step "Services et pare-feu"
& "$PSScriptRoot\services.ps1" -Action install -InstallDir $InstallDir -Port $Port
& "$PSScriptRoot\firewall.ps1" -InstallDir $InstallDir -Port $Port -LlamaDir $LlamaDir

Step "Test de bon fonctionnement"
& "$PSScriptRoot\test.ps1" -InstallDir $InstallDir -Port $Port
Write-Host "`nInstallation terminée : https://$Hostname`:$Port  (importez certs\server.crt dans les postes clients)." -ForegroundColor Green
Write-Host "Rappel : activez BitLocker sur le disque de données ($DataDir) et configurez la sauvegarde (backup.ps1)."
