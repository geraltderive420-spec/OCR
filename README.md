# OCR : PDF scanné vers DOCX éditable (français / arabe, 100 % hors ligne)

Application web interne : un utilisateur dépose un PDF scanné, l'application reconnaît le texte (Surya 2 servi par
llama.cpp), reconstruit la structure (titres, paragraphes, tableaux, en-têtes et pieds de page, images, tampons) et
produit un DOCX éditable. Une relecture côte à côte (scan / texte) et une correction facultative par un modèle Ollama
local complètent le traitement.

- `PLAN.md` : cahier de route et décisions (à jour).
- `backend/` : API FastAPI, worker, services (prétraitement, OCR, correction, DOCX), migrations Alembic, tests.
- `frontend/` : interface React + TypeScript (Vite).
- `tools/` : outils de la phase 0 (dégradation d'échantillons, évaluation chiffrée, adaptateurs de moteurs OCR).
- `deploy/station/` : export du colis hors ligne pour la station (RTX 3060), scripts d'installation et de maintenance.
- `deploy/windows/` : pare-feu et certificat HTTPS facultatifs, lancement en développement.
- `docs/` : installation, exploitation, sécurité, architecture, reprise.

## Démarrage en développement

```powershell
# Environnement : .venv-surya2 (Python 3.13, torch, surya-ocr 0.22.1) + dépendances de backend\requirements.txt
cd backend
copy .env.example .env           # puis adapter (OCR_SECRET_KEY, OCR_LLAMA_CPP_DIR...) ; OCR_OCR_BACKEND=dummy pour tester sans GPU
..\.venv-surya2\Scripts\python.exe -m app.cli seed-dev       # compte de développement (la base SQLite est créée seule)
..\.venv-surya2\Scripts\python.exe -m app.serve              # base + API + interface + worker : http://127.0.0.1:3050 (API 8085, llama.cpp 8091)
```

Base : **SQLite** (`backend/data/db/ocr.sqlite3`), aucun serveur à installer. Interface en développement avec
rechargement : `cd frontend ; npm run dev` (port 5173). Export pour la station : `export_station.bat` (voir
`docs/INSTALLATION.md`).

## Tests

```powershell
cd backend ; ..\.venv-surya2\Scripts\python.exe -m pytest        # 120+ tests (SQLite isolée pour chaque test)
cd frontend ; npm test ; npm run build
```

Chaque test a sa propre base SQLite. Un test échoue en cas d'appel réseau sortant.
