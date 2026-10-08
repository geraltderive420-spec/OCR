# Architecture

```
Navigateur (React)  ──HTTP(S)──►  FastAPI (API)  ──►  SQLite (data/db/ocr.sqlite3, WAL)
                                   │  table jobs
                                   ▼
                              Worker (1 processus, séquentiel ; supervisé par `python -m app.serve`)
                                 ├─ rendu PDF (pypdfium2, 200 DPI) + redressement + prétraitement (OpenCV)
                                 ├─ gestionnaire GPU : VRAM libre, démarrage / arrêt de llama-server (par PID)
                                 ├─ Surya 2 (client) ──► llama-server 127.0.0.1:8091 (GPU, à la demande)
                                 ├─ correction ───────► Ollama 127.0.0.1:11434 (GPU partagé, modèle choisi par l'administrateur)
                                 └─ génération DOCX (python-docx + XML)
Fichiers : data/{uploads,pages,crops,exports}/<utilisateur>/<document>/<nom aléatoire>
```

## Modèle intermédiaire

Le résultat de l'OCR est un arbre document → pages → blocs en base (`blocks.html_ocr`, `html_current`, `label`, `bbox`,
`direction`, `confidence`, `to_check`). Le DOCX est **toujours régénéré** à partir de ce modèle ; la relecture et la
correction par l'IA modifient le modèle, jamais le DOCX. `corrections` trace chaque changement (avant / après, source
`llm` ou `user`, statut `proposed|accepted|rejected|auto_applied|reverted`).

## File d'attente et reprise

Pas de Redis : table `jobs`, prise atomique par `UPDATE … WHERE status='queued'` (sûre même avec deux workers), ordre équitable
entre utilisateurs (les exports d'abord). Les documents d'un **lot** (`batches`) sont des documents ordinaires regroupés.
Annulation : `documents.cancel_requested`, lu par le worker entre deux pages. L'état est dans la base (statut de chaque page) : un worker
tué reprend là où il s'est arrêté ; un job « en cours » dont le verrou est trop ancien est remis en file. Une page en échec
n'échoue pas le document. Attente du GPU : le job est remis en file avec un délai, sans compter comme un échec.

## Pipeline

1. Upload : signature `%PDF-`, taille et pages plafonnées, quota par utilisateur, nom assaini, nom aléatoire sur disque.
2. Rendu + redressement de l'image couleur d'origine (qui sert aux découpes), puis prétraitement (profils `leger` /
   `delave` / `tres_delave`, ou `auto` selon le contraste mesuré) : les deux images restent alignées pixel à pixel.
3. OCR : mise en page + reconnaissance par bloc ; tableaux en HTML (cellules fusionnées) ; découpes d'images dans l'original.
4. Tampons / signatures : détection par couleur d'encre (HSV) si le modèle ne les a pas vus ; « à vérifier » s'ils
   recouvrent du texte.
5. Correction (facultative) : texte seul, balises remplacées par des jetons, garde-fous **dans le code** (chiffres,
   taux de modification, longueur, langue). Modèles locaux seulement.
6. Relecture (facultative) puis export DOCX.

## Correction par un modèle Ollama

Le modèle se choisit dans **Administration > Paramètres** (liste des modèles locaux ; les modèles `:cloud` sont refusés) et peut
être surchargé **par document** à l'envoi. Modèles « thinking » : paramètre `think` de l'API Ollama, raisonnement lu dans un
champ séparé et jamais enregistré ; repli automatique si le modèle ne gère pas `think`. Si Ollama est arrêté, la
correction est ignorée et le document est traité normalement.

## GPU partagé

`llama-server` est un processus du worker (pas un service permanent) : démarré à la demande, arrêté après inactivité. Voir
`INSTALLATION.md` §4. Surya sait lancer son propre serveur mais pas l'arrêter sous Windows et garde une trace périmée
(`~/.cache/datalab/surya/llamacpp_server.json`) : le worker gère donc lui-même le processus et attache Surya par URL.
