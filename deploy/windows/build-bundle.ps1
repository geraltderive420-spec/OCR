<#
.SYNOPSIS
  Prépare le paquet d'installation hors ligne (à lancer sur une machine AVEC Internet, une seule fois par version).
.DESCRIPTION
  Produit le dossier bundle\ : roues Python (pip download), interface compilée, modèles Surya 2 (GGUF) et poids de
  détection (cache Hugging Face / datalab), à copier sur le poste de production (clé USB).
  Les installateurs de Python, MariaDB, du pilote NVIDIA, des redistribuables VC++, d'Ollama, de NSSM et de 7-Zip,
  ainsi que llama-server.exe (compilé pour la carte de production, arch 86) et les modèles Ollama, sont à y copier
  à la main : voir docs/INSTALLATION.md.
#>
param(
    [string]$Bundle = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) "bundle"),
    [string]$Python = "python",
    [string]$TorchIndex = "https://download.pytorch.org/whl/cu126"
)
. "$PSScriptRoot\common.ps1"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

Step "Dossier du paquet : $Bundle"
New-Item -ItemType Directory -Force -Path "$Bundle\wheels", "$Bundle\hf" | Out-Null

Step "Roues Python (torch CUDA puis dépendances)"
& $Python -m pip download --dest "$Bundle\wheels" --index-url $TorchIndex "torch==2.13.0" "torchvision==0.28.0"
& $Python -m pip download --dest "$Bundle\wheels" -r "$root\backend\requirements.txt"

Step "Interface compilée"
Push-Location "$root\frontend"
try { npm ci; npm run build } finally { Pop-Location }
Copy-Item "$root\frontend\dist" "$Bundle\frontend-dist" -Recurse -Force

Step "Modèles Surya 2 (GGUF) dans le cache Hugging Face du paquet"
$env:HF_HOME = "$Bundle\hf"
& $Python -c "from huggingface_hub import hf_hub_download as d; [d('datalab-to/surya-ocr-2-gguf', f) for f in ('surya-2.gguf','surya-2-mmproj.gguf')]"

Step "Poids de détection de lignes (cache datalab de Surya)"
$cache = Join-Path $env:LOCALAPPDATA "datalab"
if (Test-Path $cache) { Copy-Item $cache "$Bundle\datalab" -Recurse -Force } else { Write-Warning "Cache datalab absent : lancez une fois Surya sur cette machine." }

Step "Terminé"
Write-Host "Copiez le dossier '$Bundle' sur le poste de production (hors ligne), avec les installateurs listés dans docs/INSTALLATION.md."
