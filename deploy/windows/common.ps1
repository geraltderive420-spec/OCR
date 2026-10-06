# Fonctions communes aux scripts de déploiement (PowerShell 5.1). Aucun secret n'est affiché ni journalisé.
$ErrorActionPreference = "Stop"

function Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Ok([string]$Message)   { Write-Host "  OK  $Message" -ForegroundColor Green }
function Ko([string]$Message)   { Write-Host "  KO  $Message" -ForegroundColor Red }

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Lancez ce script dans un PowerShell ouvert en tant qu'administrateur."
    }
}

function Read-DotEnv([string]$Path) {
    $h = @{}
    if (Test-Path $Path) {
        Get-Content $Path -Encoding UTF8 | ForEach-Object {
            if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { $h[$Matches[1]] = $Matches[2] }
        }
    }
    return $h
}

function New-RandomSecret([int]$Bytes = 48) {
    $b = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($b)
    return ([Convert]::ToBase64String($b)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Read-SecretText([string]$Prompt) {
    $s = Read-Host -Prompt $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-VenvPython([string]$InstallDir) { return Join-Path $InstallDir "venv\Scripts\python.exe" }
