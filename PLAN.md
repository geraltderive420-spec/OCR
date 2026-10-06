# PLAN — Application OCR PDF scanné → DOCX (français / arabe, 100 % hors ligne)

Ce document est le cahier de route destiné à Claude Code. Lis-le en entier avant d'écrire du code, puis exécute les phases dans l'ordre. Chaque phase se termine par ses critères d'acceptation ; ne passe pas à la suivante tant qu'ils ne sont pas remplis.

## 1. Objectif

Application web client-serveur, utilisée en interne sur un réseau local sans Internet. Un utilisateur connecté dépose un PDF scanné (souvent très délavé), l'application en fait l'OCR et produit un DOCX **éditable qui reprend la structure** du document : titres, paragraphes, tableaux, en-têtes et pieds de page, schémas, tampons et signatures.

## 2. Décisions arrêtées

| Sujet | Décision |
|---|---|
| Production | Windows 11, **sans Docker**, hors ligne. RTX 3060 12 Go, i9-12900, 64 Go RAM. GPU **partagé** avec 3 ou 4 autres applications de l'organisation |
| Développement | GTX 1050 Ti 4 Go, i7-8750H, 32 Go RAM |
| Pile | FastAPI, React, MariaDB |
| OCR | **Surya 2 (`surya-ocr==0.22.1`) servi par llama.cpp**, sans Docker. Retenu après comparaison avec Surya 1 (§3). PaddleOCR-VL et dots.ocr restent à tester pour l'arabe (phase 0) |
| Serveur du modèle OCR | **`llama-server` CUDA compilé pour la carte** (arch 61 en développement, 86 en production), lancé et arrêté par le worker, port local. Surya 1 en processus (modèle GED_REACT) reste le plan B. Pas de Docker, pas de vLLM |
| LLM de correction | Ollama, optionnel, compatible modèles « thinking » |
| Langues | Français et arabe, souvent mêlés sur une même page |
| Contenu | Texte imprimé, tableaux, en-têtes, schémas, tampons, signatures. Pas de manuscrit ni de formules à traiter |
| Mise en page DOCX | Structure éditable, pas de positionnement absolu par zones de texte |
| Correction LLM | Deux modes : automatique, ou propositions à valider |
| Relecture | Écran côte à côte (scan / texte), **facultatif** avant export |
| Comptes | Créés par un administrateur ; chaque utilisateur ne voit que son historique |
| Charge | 1 à 5 utilisateurs, un seul traitement GPU à la fois |

## 3. Moteur OCR : Surya 2 via llama.cpp (sans Docker)

**Décision** (mesures du 06/10/2026 sur 12 pages, profil « délavé », GTX 1050 Ti 4 Go) : Surya 2 servi par `llama-server` CUDA, sans Docker. Comparaison avec Surya 1 (en processus, réglages GED_REACT) :

| | Surya 1 | Surya 2 (llama.cpp CUDA) |
|---|---|---|
| Vitesse (1050 Ti) | 163 s / page | **93 s / page** |
| VRAM (pic) | 3 966 Mo | **2 130 Mo** |
| Mots de la référence retrouvés (ensemble / arabe / français) | 80,2 / 71,2 / 89,2 % | 80,4 / 70,3 / 90,5 % |
| Précision des mots | 80,4 % | 83,1 % |
| Tableaux détectés (pages dégradées) | 5 | **11** (= Surya 1 sur pages propres) |
| Étiquette `Handwriting` à tort | 6 | 0 |

Lecture : texte lu **équivalent** ; l'arabe reste le point faible (~70 % des mots) avec les deux versions ; la mise en page de Surya 2 résiste mieux à la dégradation ; il est plus rapide et deux fois plus léger. Le CER de page entière n'est pas fiable avec une référence issue de la couche texte d'un PDF (il mesure l'ordre du texte) : on s'appuie sur le rappel / précision des mots (`tools/evaluate.py`).

**Mise en œuvre :**
- **Environnement Python dédié** : Surya 2 exige `transformers>=5.12`, `pypdfium2>=5.10`, `huggingface-hub>=1.5`, incompatibles avec Surya 1 (`transformers==4.56.2`) de GED_REACT. Ne jamais installer les deux dans le même environnement.
- **`llama-server` compilé pour la carte** : un exécutable compilé pour une seule architecture plante sur l'autre (`CUDA error: no kernel image is available`). Développement : build `CMAKE_CUDA_ARCHITECTURES=61` (`C:\LLAMA.CPP.CUDA\llama.cpp\build\bin\Release`). Production (RTX 3060) : build arch 86. Un build « 61;86 » servirait aux deux. **Ne pas utiliser le `llama-server` winget** : c'est un build Vulkan qui s'arrête sans message à l'encodage d'image.
- **Paramètre de configuration `LLAMA_CPP_DIR`** : dossier Release de llama.cpp (contient `llama-server.exe`), propre à chaque machine (dev : arch 61, prod : arch 86). Un seul paramètre dans la configuration de l'application ; utilisé par le worker et par `tools/evaluate.py` (`--llama-cpp-dir`).
- **Modèle** : GGUF `datalab-to/surya-ocr-2-gguf` (`surya-2.gguf` 1,27 Go + `surya-2-mmproj.gguf` 205 Mo), copiés dans le bundle hors ligne, `HF_HUB_OFFLINE=1`.
- **Le worker lance lui-même `llama-server`** (arguments repris du code de Surya : `--mmproj`, `--parallel 1`, `--ctx-size`, `--jinja`, `--alias`, port local fixe sur `127.0.0.1`), et indique son adresse à Surya (`SURYA_INFERENCE_BACKEND=llamacpp`, `SURYA_INFERENCE_URL=http://127.0.0.1:<port>/v1`, `SURYA_INFERENCE_AUTOSTART=false`). Raisons : sous Windows, Surya ne sait pas arrêter le serveur qu'il a lancé (`os.kill` → WinError 87) et garde un fichier `~/.cache/datalab/surya/llamacpp_server.json` qui, périmé, provoque des « Connection error » en rafale. Avec un processus géré par le worker, l'arrêt est fiable et vérifié.
- **`SURYA_INFERENCE_PARALLEL=1`** (l'inférence parallèle ne fonctionne pas avec llama.cpp, ticket #523). Si le serveur plante à l'encodage d'image : `LLAMA_CPP_NO_MMPROJ_OFFLOAD=true`. Des « failed to parse grammar » (ticket #542) apparaissent dans le journal du serveur sans empêcher les réponses : à surveiller.

**Partage du GPU** (3 ou 4 autres applications) :
- `llama-server` est **démarré à la demande** (chargement ~8 s mesuré) et **arrêté après une inactivité courte** (2 à 5 min, paramétrable) ; besoin ~2,1 Go de VRAM.
- Avant de démarrer : contrôle de la VRAM libre (`nvidia-smi`) ; si insuffisante, le job reste en file avec le statut « en attente de GPU » et le worker réessaie.
- Ollama partagé : ne pas imposer un `keep_alive` qui décharge les modèles des autres applications ; n'agir que sur nos propres modèles ; vérifier `OLLAMA_MAX_LOADED_MODELS`.
- Verrou GPU commun si toutes les applications sont de l'organisation (à décider avec l'utilisateur).
- Ne jamais arrêter `llama-server.exe` « par nom » : cela peut couper celui d'une autre application. Arrêter uniquement le processus lancé par le worker (par PID).

**Points ouverts :** l'encodeur d'image tourne sur le processeur même avec le build CUDA (« CLIP using CPU backend ») : à comprendre, il limite la vitesse ; 41 blocs étiquetés `PageHeader` sur les 12 pages semblent invraisemblables : à vérifier ; l'arabe n'est pas amélioré.

### 3 bis. Solutions de repli

- **Surya 1 en processus** (`surya-ocr==0.16.7`, modèle GED_REACT : BF16 sur Ampere, FP32 sur Pascal, `encoder_chunk_size=4096`, lots 4/1, image ≤ 1600 px, `sort_lines=True`, `math_mode=False`, déchargement par `del` + `torch.cuda.empty_cache()`) : éprouvé en production, plus lent et plus lourd en VRAM, mais sans serveur.
- **Docker + vLLM** (image `vllm/vllm-openai:v0.20.1`, réglée par défaut pour une RTX 4090, 85 % de VRAM) : **abandonné** tant que llama.cpp suffit (licence Docker Desktop à confirmer, VRAM gourmande avec le GPU partagé, plantage intermittent au démarrage, ticket #543, port exposé malgré `SURYA_INFERENCE_HOST`, ticket #547, pas de prise en charge de la 1050 Ti).
- Licence des poids Surya : gratuite sous 5 M$ de revenu **ou de financement** ; le budget d'une administration pourrait compter comme financement : confirmation écrite de Datalab à obtenir (s'applique à Surya 1 et 2).

## 4. Risques connus (à garder en tête partout)

1. **Scans anciens** : Surya 2 obtient 41,8 % sur la catégorie « OldScan » d'olmOCR-bench, contre 83,3 % au global. Le prétraitement d'image est donc une pièce centrale, pas un bonus.
2. **Arabe** : 72,7 % sur le benchmark interne de Surya (français : 89,3 %). Prévoir des erreurs et un écran de relecture efficace.
3. **Hallucinations du LLM** : il peut « corriger » des noms propres, numéros, dates, références. Garde-fous obligatoires (§9).
4. **VRAM 12 Go partagée** entre Surya 2 (~2,1 Go), Ollama **et les autres applications de l'organisation** sur le même GPU. Démarrage à la demande de `llama-server`, arrêt après inactivité, contrôle de la VRAM libre (§3). OCR et correction s'exécutent l'une après l'autre, jamais en parallèle.
4 bis. **Fiabilité de `llama-server` sous Windows** : build à compiler pour l'architecture de la carte ; le build Vulkan winget est instable ; Surya ne sait pas arrêter son serveur ni nettoyer sa trace (§3) ; l'inférence parallèle ne fonctionne pas (#523) ; erreurs de grammaire (#542) ; substitutions de caractères aberrantes sur textes courts rapportées avec llama.cpp (#540). Tableaux rendus comme légende en OCR de page complète (#551) et texte de schémas ignoré (#544) : passer par `TableRecPredictor`.
5. **Hors ligne** : aucun téléchargement à l'exécution. Tout appel réseau sortant est un bug.

## 4 bis. Confidentialité des documents (exigence de premier rang)

Les documents traités sont confidentiels. Deux conséquences distinctes.

**A. Pendant le développement — aucun document réel ne doit atteindre Claude Code.**
Claude Code envoie à un service en ligne le contenu des fichiers qu'il lit. Donc :
- Les scans réels ne sont **jamais** placés dans le dépôt ni dans un dossier accessible à Claude Code. Ils restent dans un dossier externe (ex. `D:\scans_confidentiels\`), exclu par `.gitignore` et par les règles d'accès de Claude Code.
- `samples/` ne contient que des documents **non confidentiels** : documents publics (journaux officiels bilingues, formulaires vierges) dégradés artificiellement par un script `tools/degrade.py` (baisse de contraste, bruit, flou, inclinaison, tampons factices).
- L'évaluation sur les vrais scans est lancée **par l'utilisateur lui-même** avec `tools/evaluate.py`, qui n'écrit qu'un rapport chiffré (confiance moyenne, temps, VRAM, nombre de blocs, taux d'erreur si une transcription de référence existe). Le rapport ne contient ni texte extrait, ni image, ni nom de fichier. Seul ce rapport est communiqué à Claude Code.
- Claude Code ne doit jamais demander à voir un scan réel ni sa sortie OCR. Pour déboguer un cas, l'utilisateur reproduit le défaut sur un document factice.

**B. Dans l'application.**
- Aucun contenu de document dans les journaux : ni texte OCR, ni prompts ou réponses du LLM, ni noms de fichiers d'origine (utiliser les identifiants).
- Isolation stricte par utilisateur ; l'administrateur voit les statistiques et les métadonnées, pas le contenu.
- Fichiers stockés hors de la racine web, sous des noms aléatoires, servis uniquement par des routes authentifiées qui vérifient le propriétaire.
- Chiffrement au repos : disque de données sous BitLocker (minimum) ; chiffrement applicatif des fichiers en option, clé hors base.
- HTTPS sur le réseau local (certificat interne), cookies `Secure` + `HttpOnly` + `SameSite=Strict`.
- `llama-server`, Ollama et MariaDB écoutent uniquement sur `127.0.0.1`.
- Suppression réelle : effacement des fichiers, pages, découpes, exports et lignes en base ; purge automatique après la durée de rétention ; nettoyage des fichiers temporaires à chaque fin de job.
- Session expirée après inactivité, verrouillage après échecs de connexion, journal d'audit (connexion, dépôt, téléchargement, suppression).
- Sauvegardes chiffrées.

## 5. Architecture

```
Navigateur (React)
      │ HTTP (LAN)
FastAPI ──── MariaDB
      │            ▲
      │ table jobs │
      ▼            │
Worker (1 processus, séquentiel)
  ├─ prétraitement image (OpenCV)
  ├─ gestionnaire GPU : contrôle VRAM libre, démarrage/arrêt de llama-server (par PID), verrou commun
  ├─ Surya 2 (client Python) ──► llama-server 127.0.0.1:<port> (GPU, à la demande)
  ├─ correction ─────────────► Ollama :11434 (GPU, partagé avec d'autres applications)
  └─ génération DOCX (python-docx)
Stockage fichiers : data/{uploads,pages,crops,exports}/<user_id>/<document_id>/
```

Choix techniques :
- **Pas de Redis ni Celery** : file d'attente dans MariaDB (`SELECT … FOR UPDATE SKIP LOCKED`), suffisante pour 1 à 5 utilisateurs et simple à installer sous Windows.
- **Modèle intermédiaire JSON** : le résultat OCR est stocké comme un arbre document → pages → blocs. Le DOCX est toujours régénéré à partir de ce modèle. La relecture et la correction LLM modifient le modèle, jamais le DOCX.
- Backend : Python 3.11 ou 3.12, SQLAlchemy 2 + Alembic, Pydantic v2, pypdfium2, OpenCV, python-docx, lxml, httpx.
- Frontend : Vite + React + TypeScript, React Router, TanStack Query. Interface en français, textes dans des fichiers i18n, champs de contenu en `dir="auto"`.
- Suivi d'avancement par interrogation périodique (`GET /documents/{id}`), pas de WebSocket.

### Arborescence

```
backend/
  app/{api,core,models,schemas,services,worker}/
  app/services/{preprocess,ocr,llm,docx_builder,storage}/
  alembic/  tests/
frontend/
  src/{pages,components,api,i18n}/
deploy/windows/   # scripts PowerShell, définitions de services, bundle hors ligne
samples/          # scans réels de test (fournis par l'utilisateur, non versionnés)
docs/
```

## 6. Modèle de données (MariaDB)

- `users` : id, username, password_hash (argon2), role (`admin` | `user`), is_active, must_change_password, created_at, last_login_at
- `documents` : id, user_id, original_filename, page_count, status (`queued` | `preprocessing` | `ocr` | `correcting` | `review` | `done` | `failed`), options (JSON : profil de prétraitement, correction LLM, mode auto/validation, relecture), progress, error, timestamps
- `pages` : id, document_id, index, width, height, dpi, image_path, preprocessed_path, mean_confidence
- `blocks` : id, page_id, reading_order, label, bbox (JSON), html_ocr, html_current, confidence, crop_path (blocs image), direction (`ltr` | `rtl`)
- `corrections` : id, block_id, source (`llm` | `user`), before, after, status (`proposed` | `accepted` | `rejected` | `auto_applied`), model_name, created_at
- `jobs` : id, document_id, type, status, attempts, locked_at, error
- `exports` : id, document_id, path, created_at
- `audit_log` : id, user_id, action, target, created_at
- `settings` : clé / valeur (modèle Ollama, seuils, rétention)

Règle absolue : toute requête sur un document filtre sur `user_id` (sauf administrateur pour les statistiques, sans accès au contenu).

## 7. Pipeline de traitement

1. **Validation de l'upload** : type réel du fichier (signature PDF), taille et nombre de pages plafonnés (paramétrables), nom de fichier assaini.
2. **Rendu** : pypdfium2, 200 DPI par défaut. Conserver l'image d'origine (elle sert aux découpes d'images).
3. **Prétraitement** (OpenCV), par profils `léger` / `délavé` / `très délavé` :
   niveaux de gris → redressement → normalisation du fond (division par le fond flouté) → CLAHE → débruitage → netteté ; binarisation adaptative uniquement dans le profil le plus fort. Largeur ramenée à 2048 px maximum (recommandation Surya) ; 1600 px (valeur éprouvée dans GED_REACT) à comparer en phase 0.
   Mode `auto` : choisir le profil d'après le contraste mesuré.
4. **OCR Surya 2** : `LayoutPredictor` puis `RecognitionPredictor(manager)([image], layouts)` (blocs avec `label`, `html`, `bbox`, `confidence`), via le serveur llama.cpp du §3 ; prototype dans `tools/ocr_backends.py` (`Surya2Backend`). Pour chaque bloc `Table`, `TableRecPredictor.predict_full` sur la découpe pour obtenir le HTML avec cellules fusionnées. Vérifier ces signatures dans la version installée avant de coder.
5. **Blocs visuels** (`Picture`, `Figure`, `Diagram`) : découpe dans l'image **d'origine**, enregistrée dans `crops/`.
6. **Tampons et signatures** : s'ils ne sont pas détectés comme image, détection complémentaire par couleur d'encre (bleu / rouge en HSV) et par composantes isolées ; découpe en image, insérée à l'endroit correspondant dans l'ordre de lecture. Si un tampon recouvre du texte, marquer le bloc « à vérifier ».
7. **Construction du modèle intermédiaire** : direction de chaque bloc d'après la proportion de caractères arabes ; repérage des en-têtes et pieds répétés d'une page à l'autre.
8. **Correction LLM** (si activée) — §9.
9. **Relecture** (si activée) : statut `review`, on attend l'utilisateur.
10. **Génération DOCX** — §8.

Robustesse : une page en échec ne fait pas échouer le document (bloc marqué en erreur, visible à la relecture). Reprise possible d'un job interrompu, page par page.

## 8. Génération du DOCX (python-docx + XML direct)

- Format et orientation de page repris du scan ; marges estimées d'après l'enveloppe des blocs.
- Correspondance des étiquettes : `SectionHeader` → styles Titre (niveau déduit de la hauteur de ligne), `Text` → paragraphe, `ListGroup` → liste, `Caption` → légende, `Footnote` → paragraphe en petit corps, `Table` → tableau Word, blocs visuels → image en ligne à largeur proportionnelle.
- `PageHeader` / `PageFooter` : vrai en-tête / pied Word s'ils se répètent, sinon paragraphe dans le corps.
- Saut de page entre chaque page du scan.
- **Tableaux** : conversion du HTML (`rowspan` / `colspan` → cellules fusionnées), bordures visibles, largeurs de colonnes proportionnelles aux boîtes détectées.
- **Arabe et texte mixte** : `w:bidi` sur le paragraphe, `w:rtl` sur les segments arabes, `w:bidiVisual` sur les tableaux en arabe, police « complex script » définie (ex. une police arabe installée localement), alignement à droite. Découper les paragraphes mixtes en segments par écriture.
- Alignement (gauche, centré, droite, justifié) déduit de la position du bloc par rapport aux marges ; taille de police estimée d'après la hauteur de ligne puis arrondie à une échelle fixe.
- Mise en colonnes : version 1 = ordre de lecture en une colonne ; sections multi-colonnes en amélioration ultérieure.
- Balises `<math>` : conserver le texte tel quel (hors périmètre).

Tests : un jeu de modèles intermédiaires figés → DOCX → relecture par python-docx pour vérifier nombre de paragraphes, tableaux, fusions, attributs RTL, images.

## 9. Correction par LLM (Ollama)

- Client HTTP vers `/api/chat`, modèle choisi dans les paramètres administrateur.
- **Modèles thinking** : utiliser le paramètre `think` de l'API quand le modèle le prend en charge et lire le raisonnement dans son champ séparé ; en secours, retirer les balises `<think>…</think>` de la réponse. Le raisonnement n'est jamais enregistré dans le texte du document. Prévoir délais et plafond de tokens plus larges, et une option pour désactiver le raisonnement. Vérifier le comportement exact avec la version d'Ollama installée.
- Sortie contrainte par schéma JSON (`format`), température basse.
- Correction **bloc par bloc, texte seul** (balises HTML remplacées par des jetons puis restaurées). Les tableaux sont traités cellule par cellule.
- Ciblage : par défaut uniquement les blocs sous un seuil de confiance ; option « tout corriger ».
- Garde-fous, appliqués par le code et non par le prompt :
  - rejet si les chiffres, dates ou jetons changent ;
  - rejet si le taux de modification dépasse un seuil (15 % par défaut) ;
  - rejet si la longueur ou la langue du bloc change nettement ;
  - toute correction est tracée dans `corrections` avec avant / après.
- Mode automatique : corrections valides appliquées (`auto_applied`), toujours annulables. Mode validation : `proposed`, affichées en différences à l'écran de relecture.
- Mémoire GPU : la correction démarre après la fin de l'OCR de tout le document. Régler `keep_alive` d'Ollama pour libérer la VRAM après usage.

## 10. API (préfixe `/api`)

- Auth : `POST /auth/login`, `POST /auth/logout`, `POST /auth/refresh`, `POST /auth/change-password`, `GET /auth/me` — jeton d'accès court + rafraîchissement en cookie httpOnly, limitation des tentatives de connexion.
- Documents : `POST /documents` (upload + options), `GET /documents` (historique paginé), `GET /documents/{id}`, `DELETE /documents/{id}`, `POST /documents/{id}/retry`
- Relecture : `GET /documents/{id}/pages/{n}` (image + blocs), `PATCH /blocks/{id}`, `POST /corrections/{id}/accept|reject`, `POST /documents/{id}/finalize`
- Export : `GET /documents/{id}/export.docx`
- Admin : CRUD utilisateurs, réinitialisation de mot de passe, activation, statistiques d'usage, paramètres, état des services (llama-server, Ollama, GPU, disque)

## 11. Écrans

1. Connexion (changement de mot de passe imposé à la première connexion)
2. Nouveau document : dépôt du PDF, profil de prétraitement, correction LLM (non / automatique / à valider), relecture (oui / non)
3. Historique : liste, statut, avancement, téléchargement, suppression
4. Détail / relecture : scan à gauche avec boîtes des blocs, texte éditable à droite, synchronisation au survol, propositions du LLM en différences avec accepter / refuser, blocs « à vérifier » mis en avant, navigation par page
5. Administration : utilisateurs, statistiques, paramètres, état des services

## 12. Phases de réalisation

### Phase 0 — Validation technique (bloquante)
**Fait (06/10/2026)** : `tools/degrade.py`, `tools/evaluate.py`, `tools/extract_reference.py`, `tools/ocr_backends.py` ; échantillons (`samples/` : 10 numéros du Journal officiel algérien, pages dégradées « délavé » et « très délavé ») ; comparaison Surya 1 / Surya 2 sur 12 pages (§3) ; contrôle sur pages propres pour Surya 1 (la dégradation « délavé » coûte ~2 à 3 points de mots, mais fait perdre la moitié des tableaux détectés et ajoute de faux `Handwriting`) ; `llama-server` CUDA arch 61 validé sur la 1050 Ti.
**Reste à faire :**
- **Arabe** : comparer sur les mêmes échantillons PaddleOCR-VL-1.6 (~2 Go de VRAM) et dots.ocr ; option : Surya pour la mise en page + autre modèle pour lire l'arabe. L'utilisateur juge PaddleOCR classique moins fiable que Surya (GED_REACT) ; PaddleOCR-VL est un modèle différent, à tester sans a priori.
- **Prétraitement d'image** (§7) : écrire les profils OpenCV et mesurer s'ils récupèrent les tableaux perdus et suppriment le faux `Handwriting`, surtout sur le profil `très délavé`.
- **Surya 2 sur pages propres** (séparer l'effet de la dégradation) ; vérifier les 41 `PageHeader` ; comprendre pourquoi l'encodeur d'image reste sur le processeur ; taille d'image (1600 vs 2048 px).
- **RTX 3060** : refaire les mesures de vitesse et de VRAM avec le build arch 86 (seules valables pour la production).
- Vérifier la cohabitation avec les autres applications du GPU : démarrage/arrêt réels de `llama-server`, contrôle de la VRAM, Ollama partagé, choix du verrou GPU commun.
- Tester 2 ou 3 modèles Ollama candidats (dont un modèle thinking, bon en arabe, tenant dans environ 8 Go) sur des sorties OCR réelles.
- L'utilisateur lance `tools/evaluate.py` sur ses vrais scans (hors dépôt) et transmet le rapport chiffré.
- Livrable : `docs/phase0-rapport.md` avec chiffres, choix retenus et décision. **S'arrêter et demander validation à l'utilisateur avant la phase 1.**

### Phase 1 — Socle
Dépôt, configuration par variables d'environnement, MariaDB + Alembic, modèles, authentification, rôles, commande de création du premier administrateur, journal d'audit.
*Acceptation* : tests d'authentification et d'isolation entre utilisateurs au vert.

### Phase 2 — Upload, file d'attente, prétraitement
Upload validé, stockage, worker avec verrouillage et reprise, rendu des pages, profils de prétraitement.
*Acceptation* : un PDF déposé produit ses images prétraitées ; un worker tué reprend le job.

### Phase 3 — OCR et modèle intermédiaire
Intégration Surya, tableaux, découpes d'images, tampons et signatures, direction des blocs.
*Acceptation* : JSON intermédiaire complet sur tous les échantillons, sans appel réseau sortant.

### Phase 4 — Générateur DOCX
*Acceptation* : tests du §8 au vert ; ouverture sans erreur dans Word ; arabe correctement orienté.

### Phase 5 — Interface de base
Connexion, nouveau document, historique, avancement, téléchargement.
*Acceptation* : parcours complet upload → DOCX depuis le navigateur.

### Phase 6 — Correction LLM
*Acceptation* : tests des garde-fous (un changement de chiffre est rejeté), modes automatique et validation, fonctionnement avec et sans thinking, application utilisable si Ollama est arrêté.

### Phase 7 — Écran de relecture

### Phase 8 — Administration, statistiques, rétention
Purge automatique des fichiers après N jours (paramétrable), quotas d'espace.

### Phase 9 — Déploiement Windows hors ligne
- Bundle : installateurs (Python, MariaDB, pilote NVIDIA, redistribuables VC++), **`llama-server` CUDA compilé pour la carte de production (arch 86) et ses DLL**, **GGUF Surya 2 (modèle + mmproj)**, poids de détection copiés (cache `datalab`), dossier de wheels (`pip download`, dont torch CUDA), frontend déjà compilé servi par FastAPI, modèles Ollama copiés, police arabe.
- Variables `HF_HUB_OFFLINE=1` et `TRANSFORMERS_OFFLINE=1`.
- Services Windows (NSSM ou WinSW) : MariaDB, Ollama, API, worker, avec redémarrage automatique (`llama-server` est lancé et arrêté par le worker).
- Scripts PowerShell : installation, test de bon fonctionnement, sauvegarde base + fichiers, mise à jour.
- *Acceptation* : installation complète sur une machine **câble réseau débranché**, puis parcours de bout en bout.

## 13. Règles de travail pour Claude Code

- Vérifier chaque API de Surya, llama.cpp et Ollama dans la version réellement installée ; ne rien supposer de mémoire. Figer les versions.
- Aucun téléchargement à l'exécution ; ajouter un test qui échoue en cas d'appel réseau sortant.
- Tests à chaque phase, un commit par étape cohérente, `docs/` tenu à jour.
- Ne jamais écrire de secret dans le dépôt ; fournir un `.env.example`.
- Ne jamais lire, demander ou afficher un document réel ni sa sortie OCR (§4 bis). Travailler uniquement sur `samples/` et sur les rapports chiffrés.
- En cas d'écart nécessaire par rapport à ce plan, l'expliquer et demander avant d'agir.

## 14. À fournir par l'utilisateur avant la phase 0

- Dans `samples/` : pages **non confidentielles** (fait : 10 numéros du Journal officiel algérien, 30 pages dégradées par profil).
- Hors dépôt : un lot de vrais scans, gardé par l'utilisateur, sur lequel il lance lui-même `tools/evaluate.py` (voir §4 bis).
- Système de la machine de développement (Windows ou Linux).
- Version de Windows et du pilote NVIDIA sur la machine de production.
- Confirmation écrite de Datalab pour la licence des poids Surya (seuil de 5 M$ de revenu **ou de financement** : le budget de l'administration pourrait compter comme financement). Concerne aussi Surya 1.
- Les autres applications du GPU sont-elles toutes de l'organisation, sur le même Ollama ? (décide du verrou GPU commun, §3)
- Taille maximale habituelle d'un PDF (pages).
