<#
.SYNOPSIS  Lance l'environnement de développement : API (8085), interface (3050) et worker (python -m app.serve) ; interface Vite avec rechargement (5173).
.EXAMPLE   .\start-dev.ps1 -Python ..\..\.venv-surya2\Scripts\python.exe
#>
param(
    [string]$Python = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) ".venv-surya2\Scripts\python.exe")
)
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$backend = Join-Path $root "backend"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backend'; & '$Python' -m app.serve"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$(Join-Path $root 'frontend')'; npm run dev"
Write-Host "API + worker : http://127.0.0.1:3050 (API : 8085)   Interface (dev, avec rechargement) : http://127.0.0.1:5173"
Write-Host "Premier administrateur : cd backend ; python -m app.cli create-admin <identifiant>  (ou : python -m app.cli seed-dev)"
