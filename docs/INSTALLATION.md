# Installation hors ligne sur le poste de production (Windows)

Poste cible : Windows 11, RTX 3060 12 Go, sans Docker, **sans Internet**. L'installation se fait depuis un dossier `bundle\`
préparé une fois sur une machine connectée (clé USB).

## 1. Préparer le paquet (machine avec Internet)

```powershell
cd deploy\windows
.\build-bundle.ps1 -Bundle D:\bundle
```

Le script télécharge les roues Python (dont `torch` CUDA 12.6), compile l'interface, et récupère les modèles Surya 2
(GGUF) et les poids de détection dans le cache. Il faut y ajouter **à la main** (installateurs, à copier dans le dossier) :

| Élément | Remarque |
|---|---|
| Python 3.11 à 3.13 (64 bits) | « Add to PATH » |
| MariaDB 11.x | service nommé `MariaDB`, mot de passe root à connaître |
| Pilote NVIDIA + redistribuables VC++ | Surya et llama.cpp en ont besoin |
| Ollama + modèles locaux | copier le dossier `.ollama\models` ; **ne jamais choisir un modèle `:cloud`** |
| NSSM | gestionnaire de services (`nssm.exe` dans le PATH) |
| 7-Zip | sauvegardes chiffrées |
| `llama-server.exe` compilé pour la RTX 3060 | `CMAKE_CUDA_ARCHITECTURES=86`, dossier Release complet avec ses DLL |

> `llama-server` doit être compilé **pour la carte où il tourne** : un build arch 86 plante sur une GTX 1050 Ti
> (`CUDA error: no kernel image`), et un build arch 61 est inutile sur la RTX 3060. Le `llama-server` livré par winget est
> un build Vulkan instable : ne pas l'utiliser.

## 2. Installer (poste de production)

PowerShell **administrateur**, câble réseau débranché si l'on veut prouver le fonctionnement hors ligne :

```powershell
cd deploy\windows
.\install.ps1 -Bundle D:\bundle -Hostname ocr.mon-organisation.local -Ip 192.168.1.20 `
              -InstallDir C:\ocr-app -DataDir D:\ocr_data -LlamaDir "C:\llama.cpp\Release"
```

Le script : vérifie les prérequis, copie l'application, crée l'environnement Python depuis les roues locales, crée la base
`ocr` et l'utilisateur MariaDB dédié `ocr_app` (mot de passe aléatoire), écrit `.env` (droits restreints, secrets
aléatoires), applique les migrations, crée le premier administrateur, génère le certificat HTTPS interne, installe les
services `OcrApi` et `OcrWorker`, règle le pare-feu, puis exécute `test.ps1`.

## 3. Après l'installation

1. **BitLocker** sur le disque de données (`D:`) : obligatoire pour le chiffrement au repos (voir `SECURITE.md`).
2. Importer `C:\ocr-app\certs\server.crt` dans les « Autorités de certification racines de confiance » des postes clients.
3. Se connecter sur `https://<nom>:8443`, changer le mot de passe provisoire, puis dans **Administration > Paramètres** :
   choisir le **modèle Ollama** de correction, les durées de conservation, les quotas, l'arrêt du moteur OCR après inactivité.
4. Planifier `backup.ps1` (tâche planifiée quotidienne) et conserver le mot de passe de sauvegarde hors du poste.
5. Lancer `.\test.ps1` : tous les contrôles doivent être au vert.

## 4. Accès GPU partagé

La carte sert aussi à d'autres applications. L'application ne garde **jamais** la VRAM inutilement : `llama-server` est
démarré à la demande (≈ 8 s) et arrêté après `gpu_idle_unload_minutes` d'inactivité ; avant de démarrer, le worker
vérifie que `gpu_min_free_mb` de VRAM sont libres (sinon le document passe en « En attente du GPU » et reprend seul).
Le modèle Ollama de correction est déchargé juste après usage (`keep_alive` = 0 par défaut).
