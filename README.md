# OCR : PDF scanné vers DOCX éditable (français / arabe, 100 % hors ligne)

Application web interne : un utilisateur dépose un PDF scanné, l'application reconnaît le texte (Surya 2 servi par
llama.cpp), reconstruit la structure (titres, paragraphes, tableaux, en-têtes et pieds de page, images, tampons) et
produit un DOCX éditable. Une relecture côte à côte (scan / texte) et une correction facultative par un modèle Ollama
local complètent le traitement.

- `PLAN.md` : cahier de route et décisions (à jour).
- `backend/` : API FastAPI, worker, services (prétraitement, OCR, correction, DOCX), migrations Alembic, tests.
- `frontend/` : interface React + TypeScript (Vite).
- `tools/` : outils de la phase 0 (dégradation d'échantillons, évaluation chiffrée, adaptateurs de moteurs OCR).
- `deploy/windows/` : installation hors ligne, services, pare-feu, sauvegarde, mise à jour.
- `docs/` : installation, exploitation, sécurité, architecture, reprise.

## Démarrage en développement

```powershell
# Environnement : .venv-surya2 (Python 3.13, torch, surya-ocr 0.22.1) + dépendances de backend\requirements.txt
cd backend
copy .env.example .env           # puis adapter (base, OCR_LLAMA_CPP_DIR...) ; OCR_OCR_BACKEND=dummy pour tester sans GPU
..\.venv-surya2\Scripts\python.exe -m alembic upgrade head
..\.venv-surya2\Scripts\python.exe -m app.cli create-admin admin
..\deploy\windows\start-dev.ps1   # API :8000, worker, interface :5173
```

## Tests

```powershell
cd backend ; ..\.venv-surya2\Scripts\python.exe -m pytest        # 65+ tests ; MariaDB de test si OCR_TEST_DATABASE_URL est défini
cd frontend ; npm test ; npm run build
```

Les tests de l'API tournent sur MariaDB réelle (base `ocr_test`) ou SQLite (`OCR_TEST_SQLITE=1`). Un test échoue en cas
d'appel réseau sortant.
