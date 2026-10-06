<#
.SYNOPSIS  Règles du pare-feu Windows : entrée HTTPS depuis le réseau local seulement, sortie bloquée pour l'application.
.DESCRIPTION
  Le trafic bouclé (127.0.0.1) n'est pas filtré par le pare-feu : MariaDB, Ollama et llama-server restent joignables
  en local, mais l'application et ses programmes ne peuvent joindre aucun autre hôte (PLAN.md §4 bis).
#>
param(
    [string]$InstallDir = "C:\ocr-app",
    [int]$Port = 8443,
    [string]$LlamaDir = "C:\LLAMA.CPP.CUDA\llama.cpp\build\bin\Release",
    [string]$LocalSubnet = "LocalSubnet"
)
. "$PSScriptRoot\common.ps1"
Require-Admin

Step "Entrée : HTTPS $Port depuis $LocalSubnet seulement"
Get-NetFirewallRule -DisplayName "OCR - HTTPS entrant" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "OCR - HTTPS entrant" -Direction Inbound -Protocol TCP -LocalPort $Port -RemoteAddress $LocalSubnet -Action Allow -Profile Domain, Private | Out-Null

Step "Sortie bloquée pour l'application, llama-server et Ollama"
$programs = @{
    "OCR - sortie bloquée (python)"       = (Get-VenvPython $InstallDir)
    "OCR - sortie bloquée (llama-server)" = (Join-Path $LlamaDir "llama-server.exe")
    "OCR - sortie bloquée (ollama)"       = (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe")
}
foreach ($name in $programs.Keys) {
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if (Test-Path $programs[$name]) {
        New-NetFirewallRule -DisplayName $name -Direction Outbound -Program $programs[$name] -Action Block | Out-Null
        Ok $name
    } else { Write-Warning "Introuvable, règle non créée : $($programs[$name])" }
}
Write-Host "Ollama : désactivez aussi ses mises à jour automatiques dans son application."
