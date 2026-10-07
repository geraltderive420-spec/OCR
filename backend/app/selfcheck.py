"""Auto-diagnostic (python -m app.cli check) : vérifie la configuration sans jamais afficher de secret ni de contenu."""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from app.core.config import BACKEND_DIR, DEFAULT_SECRET, get_settings
from app.core.db import get_engine

OK, WARN, KO = "OK", "AVERTISSEMENT", "KO"


@dataclass
class Result:
    level: str
    message: str


def _alembic_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001
        return None


def run_checks() -> list[Result]:
    s = get_settings()
    out: list[Result] = []
    add = lambda level, msg: out.append(Result(level, msg))  # noqa: E731

    # Sécurité
    if s.secret_key == DEFAULT_SECRET:
        add(KO, "OCR_SECRET_KEY est la valeur par défaut : générez une clé aléatoire.")
    else:
        add(OK, "Clé de signature personnalisée")
    add(OK if s.cookie_secure else WARN, "Cookies Secure activés (HTTPS)" if s.cookie_secure else "OCR_COOKIE_SECURE=false : à activer derrière HTTPS")
    if os.environ.get("HF_HUB_OFFLINE") == "1":
        add(OK, "Mode hors ligne (HF_HUB_OFFLINE=1)")
    else:
        add(WARN, "HF_HUB_OFFLINE n'est pas défini à 1")

    # Base et migrations
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
            add(OK, "Base de données joignable")
            try:
                current = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:  # noqa: BLE001
                current = None
        head = _alembic_head()
        if current is None:
            add(KO, "Migrations non appliquées (alembic upgrade head)")
        elif head and current != head:
            add(KO, f"Migrations en retard ({current} au lieu de {head})")
        else:
            add(OK, "Migrations à jour")
    except Exception as e:  # noqa: BLE001
        add(KO, f"Base de données injoignable ({type(e).__name__})")

    # Stockage
    try:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=s.data_dir, delete=True):
            pass
        add(OK, "Dossier de données accessible en écriture")
        free_gb = shutil.disk_usage(s.data_dir).free / 1024 ** 3
        add(OK if free_gb >= 5 else WARN, f"Espace disque libre : {free_gb:.1f} Go")
    except OSError as e:
        add(KO, f"Dossier de données inaccessible ({type(e).__name__})")

    # Moteur OCR
    if s.ocr_backend == "surya2":
        try:
            from app.services.gpu import _gguf_paths, find_binary, free_vram_mb

            add(OK, "llama-server présent") if find_binary() else None
            _gguf_paths()
            add(OK, "Modèles GGUF de Surya 2 présents dans le cache local")
            if s.layout_refine:
                from platformdirs import user_cache_dir

                det = Path(user_cache_dir("datalab")) / "models" / "text_detection"
                found = det.is_dir() and any(p.is_file() for p in det.rglob("*"))
                add(OK if found else WARN, "Détecteur de lignes présent (recalage de la mise en page)" if found else
                    "Détecteur de lignes absent du cache datalab : la mise en page ne sera pas recalée (voir build-bundle.ps1)")
            vram = free_vram_mb()
            add(OK if vram is not None else WARN, f"VRAM libre : {vram} Mo" if vram is not None else "GPU non détecté (nvidia-smi)")
        except Exception as e:  # noqa: BLE001
            add(KO, str(e) if e.__class__.__name__ == "LlamaServerError" else f"Moteur OCR indisponible ({type(e).__name__})")
    else:
        add(WARN, f"Moteur OCR « {s.ocr_backend} » (démonstration, pas de reconnaissance réelle)")

    # Ollama (facultatif)
    try:
        import httpx

        tags = httpx.get(f"{s.ollama_url}/api/tags", timeout=4).json().get("models", [])
        add(OK, f"Ollama joignable ({len(tags)} modèle(s))")
    except Exception:  # noqa: BLE001
        add(WARN, "Ollama injoignable : la correction par l'IA sera ignorée (l'application reste utilisable)")
    return out


def main() -> int:
    results = run_checks()
    for r in results:
        print(f"  {r.level:<14} {r.message}")
    ko = sum(r.level == KO for r in results)
    print(f"{ko} contrôle(s) en échec." if ko else "Auto-diagnostic réussi.")
    return 1 if ko else 0
