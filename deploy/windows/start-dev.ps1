<#
.SYNOPSIS  Lance l'environnement de développement : API (8000), worker et interface Vite (5173), dans trois fenêtres.
.EXAMPLE   .\start-dev.ps1 -Python ..\..\.venv-surya2\Scripts\python.exe
#>
param(
    [string]$Python = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) ".venv-surya2\Scripts\python.exe")
)
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$backend = Join-Path $root "backend"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backend'; & '$Python' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backend'; & '$Python' -m app.worker.runner"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$(Join-Path $root 'frontend')'; npm run dev"
Write-Host "API : http://127.0.0.1:8000   Interface (dev) : http://127.0.0.1:5173"
Write-Host "Premier administrateur : cd backend ; python -m app.cli create-admin <identifiant>"
