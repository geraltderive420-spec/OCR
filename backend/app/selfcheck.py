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
    local_only = s.api_host in ("127.0.0.1", "localhost", "::1")
    if s.cookie_secure:
        add(OK, "Cookies Secure activés (HTTPS)")
    else:
        add(OK if local_only else WARN, "Accès local seulement (127.0.0.1), HTTP suffisant" if local_only else
            "OCR_COOKIE_SECURE=false alors que l'application est ouverte au réseau : activez HTTPS")
    if os.environ.get("HF_HUB_OFFLINE") == "1":
        add(OK, "Mode hors ligne (HF_HUB_OFFLINE=1)")
    else:
        add(WARN, "HF_HUB_OFFLINE n'est pas défini à 1")

    # Base et migrations
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
            add(OK, "Base de données joignable")
            if c.dialect.name == "sqlite":
                mode = c.exec_driver_sql("PRAGMA journal_mode").scalar()
                quick = c.exec_driver_sql("PRAGMA quick_check").scalar()
                add(OK if quick == "ok" else KO, "Base SQLite intègre" if quick == "ok" else
                    "Base SQLite abîmée (quick_check) : restaurez la dernière sauvegarde")
                if str(mode).lower() != "wal":
                    add(WARN, f"Journal SQLite en mode {mode} (WAL attendu)")
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
    from app.services.ocr_config import OcrConfig

    cfg = OcrConfig.load()
    if cfg.backend == "surya2":
        try:
            from app.services import diagnostics
            from app.services.gpu import gpu_info

            rep = diagnostics.binary_report(cfg.llama_cpp_dir)
            if rep["ok"]:
                add(OK, f"llama-server démarre ({rep['version']})")
            elif cfg.cpu_fallback and diagnostics.binary_report(cfg.llama_cpp_cpu_dir)["ok"]:
                add(WARN, f"llama-server GPU inutilisable ({rep['version']}) : repli sur le build processeur (lent)")
            else:
                add(KO, f"llama-server inutilisable : {rep['version']} (Administration > Moteur OCR)")
            add(OK if diagnostics.surya_gguf_present(cfg) else KO,
                "Modèles GGUF de Surya 2 présents dans le cache local" if diagnostics.surya_gguf_present(cfg) else
                "Fichiers GGUF de Surya 2 absents du cache local (HF_HOME)")
            if cfg.layout_refine:
                found = diagnostics.line_detector_present()
                add(OK if found else WARN, "Détecteur de lignes présent (recalage de la mise en page)" if found else
                    "Détecteur de lignes absent (MODEL_CACHE_DIR) : la mise en page ne sera pas recalée")
            if cfg.layout_heron:
                found = diagnostics.heron_present()
                add(OK if found else WARN, "Détecteur Heron présent (second avis sur la mise en page)" if found else
                    "Détecteur Heron absent du cache : pas de second avis sur la mise en page")
            if cfg.second_reader:
                found = diagnostics.second_reader_present()
                add(OK if found else WARN, "Second lecteur PaddleOCR-VL présent (relecture des blocs douteux)" if found
                    else "Second lecteur PaddleOCR-VL absent du cache : pas de seconde lecture")
            cards = gpu_info()
            if cards:
                c0 = cards[0]
                add(OK, f"GPU : {c0['name']}, {c0['free_mb']} / {c0['total_mb']} Mo libres, pilote {c0['driver']}")
            else:
                add(WARN, "GPU non détecté (nvidia-smi) : " + ("OCR sur le processeur, lent" if cfg.cpu_fallback else
                                                               "l'OCR ne pourra pas démarrer"))
        except Exception as e:  # noqa: BLE001
            add(KO, f"Moteur OCR indisponible ({type(e).__name__})")
    else:
        add(WARN, f"Moteur OCR « {cfg.backend} » (démonstration, pas de reconnaissance réelle)")

    # Worker
    from app.worker.runner import read_status

    w = read_status()
    if w is None:
        add(WARN, "Le worker n'a encore jamais tourné (python -m app.serve le démarre)")
    else:
        add(OK if w.get("alive") else WARN, "Worker actif" if w.get("alive") else
            "Worker arrêté : les documents resteront en attente (python -m app.serve)")

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
