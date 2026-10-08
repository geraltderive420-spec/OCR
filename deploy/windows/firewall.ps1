<#
.SYNOPSIS  Pare-feu Windows (facultatif) : entrée depuis le réseau local seulement, sortie bloquée pour l'application.
.DESCRIPTION
  Utile seulement si l'application est ouverte au réseau (OCR_API_HOST=0.0.0.0) ou sur un poste qui n'est pas
  physiquement isolé. Le trafic bouclé (127.0.0.1) n'est pas filtré : Ollama et llama-server restent joignables en
  local, mais l'application et ses programmes ne peuvent joindre aucun autre hôte (PLAN.md §4 bis).
  À lancer dans un PowerShell administrateur.
.EXAMPLE   .\firewall.ps1 -KitDir C:\OCR -Port 8000
#>
param(
    [string]$KitDir = "C:\OCR",
    [int]$Port = 8000,
    [string]$LocalSubnet = "LocalSubnet",
    [switch]$AllowInbound
)
. "$PSScriptRoot\common.ps1"
Require-Admin

if ($AllowInbound) {
    Step "Entrée : port $Port depuis $LocalSubnet seulement"
    Get-NetFirewallRule -DisplayName "OCR - entrant" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "OCR - entrant" -Direction Inbound -Protocol TCP -LocalPort $Port -RemoteAddress $LocalSubnet -Action Allow -Profile Domain, Private | Out-Null
    Ok "port $Port ouvert au réseau local"
}

Step "Sortie bloquée pour l'application, llama-server et Ollama"
$programs = [ordered]@{
    "OCR - sortie bloquée (python)"          = (Join-Path $KitDir ".venv\Scripts\python.exe")
    "OCR - sortie bloquée (llama-server)"    = (Join-Path $KitDir "llama\llama-server.exe")
    "OCR - sortie bloquée (llama-server cpu)" = (Join-Path $KitDir "llama-cpu\llama-server.exe")
    "OCR - sortie bloquée (ollama)"          = (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe")
}
foreach ($name in $programs.Keys) {
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if (Test-Path $programs[$name]) {
        New-NetFirewallRule -DisplayName $name -Direction Outbound -Program $programs[$name] -Action Block | Out-Null
        Ok $name
    } else { Write-Warning "Introuvable, règle non créée : $($programs[$name])" }
}
Write-Host "Ollama : désactivez aussi ses mises à jour automatiques dans son application."
