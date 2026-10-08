# Installation hors ligne sur la station de production (Windows)

Poste cible : Windows 11, RTX 3060 12 Go, **sans Internet**, ni pendant l'installation ni ensuite. Aucune base de
données à installer : SQLite (un fichier) est créée au premier démarrage. L'installation se fait depuis un colis
autonome, fabriqué une fois par version sur la machine de développement (connectée).

## 1. Fabriquer le colis (machine connectée)

```powershell
.\export_station.bat                        # colis dans transfert_station\
$env:OCR_EXPORT_DIR = "D:\ocr_export\transfert_station"; .\export_station.bat   # ailleurs (~10 Go nécessaires)
```

Ce que fait `deploy/station/export_kit.py` (journal affiché, s'arrête au premier problème bloquant) :

| Étape | Contenu |
|---|---|
| Dépendances | fermeture transitive de `backend/requirements.txt` aux versions testées ; chaque module importé par le code doit y figurer |
| Roues Python | `pip download` pour Windows x64 / CPython 3.13 ; torch et torchvision en CUDA 12.6 ; **une roue exacte par paquet verrouillé, sinon l'export est refusé** |
| Python, Visual C++ | installeur Python 3.13 (python.org), runtime Visual C++ (jamais bloquant) |
| Modèles | Surya 2 (GGUF) obligatoire ; Heron, PaddleOCR-VL et détecteur de lignes (améliorations) ; copiés du cache local, sinon téléchargés |
| llama.cpp | `llama-server.exe` et ses DLL (`OCR_LLAMA_CPP_DIR` de `backend/.env`, ou `--llama-dir`) + DLL CUDA 12 ; les architectures embarquées sont lues avec `cuobjdump` |
| Interface | `frontend/dist` recompilé si les sources sont plus récentes |
| Contrôle | `empreintes.sha256` (SHA-256 de chaque fichier) |

Options : `--skip-models`, `--skip-wheels` (réutilise le cache de préparation, sans jamais livrer un colis incomplet),
`--skip-hash`, `--llama-cpu-dir D` (build processeur de secours), `--with-recognition` (poids Surya 1, +3 Go).
`export_station.bat --maj` fabrique une **mise à jour légère** (`maj_station\`).

> **llama-server et la RTX 3060.** Le build de développement est compilé pour l'architecture 61 (GTX 1050 Ti). Sur la
> RTX 3060 (architecture 86), il ne fonctionne que si le fichier contient du code intermédiaire (PTX) que le pilote
> recompile au premier lancement (plusieurs minutes, une seule fois) : l'export affiche les architectures trouvées et
> avertit si ni `sm_86` ni PTX n'y sont. Pour la vitesse maximale, compilez un build 86 sur une machine connectée avec
> `deploy\station\build-llama.bat`, puis `export_station.bat --llama-dir <dossier>`. **À valider sur la station.**

## 2. Installer (station)

1. Copier le dossier du colis sur le disque local (par exemple `C:\OCR`), pas depuis la clé.
2. `verifier_colis.bat` : contrôle d'intégrité (une copie USB tronquée ne se voit pas autrement).
3. `import_station.bat` : Python (si absent), Visual C++, intégrité, environnement virtuel, dépendances **hors ligne**,
   configuration (`backend\.env` avec clé secrète aléatoire, jamais écrasé), base SQLite, premier administrateur,
   diagnostic. Relançable sans danger après une correction : tout ce qui est en place est réutilisé.
4. `test_ocr.bat` : lit une page de test avec le vrai moteur (GPU + modèles + llama-server). **À faire avant la mise
   en service.**
5. `demarrer_ocr.bat` (et `demarrage_auto.bat installer` pour le lancement à l'ouverture de session).

## 3. Après l'installation

1. **BitLocker** sur le disque des données (voir `SECURITE.md`). Données ailleurs que dans le colis : décommenter
   `OCR_DATA_ROOT` dans `ocr_env.bat` avant le premier démarrage.
2. Se connecter sur `http://127.0.0.1:3050` (interface ; l'API est sur 8085, llama.cpp sur 8091, le second lecteur sur 8092 ;
   ports et adresses d'écoute réglables dans `ocr_env.bat`), changer le mot de passe provisoire, puis **Administration** :
   - **Moteur OCR** : chemin de llama.cpp (bouton « Vérifier »), chemin de secours processeur, largeur des pages
     Surya, slots en parallèle (essayer 2 puis 4 sur la RTX 3060), seconde lecture, etc. ;
   - **Paramètres** : modèle Ollama de correction, conservation, quotas.
3. Planifier `sauvegarder.bat` (tâche planifiée) vers un **autre disque**.

## 4. Accès depuis d'autres postes

La station écoute sur **0.0.0.0** pour l'interface (3050) et l'API (8085), en HTTP. `llama-server` (8091, second lecteur
8092) et Ollama restent sur **127.0.0.1** : seul le worker de la station les appelle, ils n'ont aucune authentification.
Lancer `ouvrir_pare_feu.bat` en administrateur (autorise 3050 et 8085 en entrée sur les réseaux privé et domaine), puis
ouvrir `http://<adresse IP de la station>:3050` depuis les autres postes. Pour revenir à un accès local seulement :
remplacer `0.0.0.0` par `127.0.0.1` dans `ocr_env.bat` (`OCR_API_HOST`) et `ouvrir_pare_feu.bat supprimer`.

> **À savoir.** En HTTP, les mots de passe et les documents circulent en clair sur le réseau local. Pour du HTTPS :
> `python make_cert.py <nom> <ip>` (python du venv), puis `OCR_SSL_CERTFILE`, `OCR_SSL_KEYFILE`, `OCR_COOKIE_SECURE=true`
> dans `app\backend\.env`, et importer `server.crt` dans les postes clients.

## 5. Accès GPU partagé

La carte sert aussi à d'autres applications. `llama-server` est démarré à la demande et arrêté après
`gpu_idle_unload_minutes` d'inactivité ; avant de démarrer, le worker vérifie que `gpu_min_free_mb` de VRAM sont libres
(sinon le document passe en « En attente du GPU » et reprend seul). Sans GPU utilisable, l'OCR passe sur le processeur
(lent) si « Repli sur le processeur » est activé. Le worker ne peut pas être lancé deux fois sur les mêmes données.

## 6. Mise à jour

Machine source : `export_station.bat --maj` → `maj_station\` (code, interface, roues des seules dépendances modifiées).
Station : `appliquer_maj.bat C:\OCR` (intégrité, application arrêtée, **sauvegarde**, code, dépendances, migration,
diagnostic). Si torch ou les modèles changent : nouvel export complet.
