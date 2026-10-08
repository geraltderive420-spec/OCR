"""Paramètres modifiables par l'administrateur (table `settings`), avec valeurs par défaut et validation."""
from __future__ import annotations

import re
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Setting

CLOUD_RE = re.compile(r"(:|-)cloud$", re.IGNORECASE)

# clé -> (défaut, type, minimum, maximum)
DEFAULTS: dict[str, tuple[Any, type, float | None, float | None]] = {
    "ollama_model": ("gemma4:e2b", str, None, None),  # modèle de correction ; jamais un modèle « cloud »
    "llm_think": (False, bool, None, None),  # laisser un modèle « thinking » raisonner (plus lent)
    "llm_confidence_threshold": (0.85, float, 0.0, 1.0),  # corriger seulement les blocs sous ce seuil
    "llm_max_change_ratio": (0.15, float, 0.01, 1.0),  # rejet au-delà de ce taux de modification
    "llm_timeout_seconds": (180, int, 10, 3600),
    "llm_keep_alive_seconds": (0, int, 0, 3600),  # 0 = décharger notre modèle après usage
    "default_preprocess": ("auto", str, None, None),  # auto | leger | delave | tres_delave
    "max_upload_mb": (100, int, 1, 2048),
    "max_pages": (300, int, 1, 5000),
    "retention_days": (30, int, 1, 3650),
    "quota_mb_per_user": (2048, int, 10, 1048576),
    "gpu_idle_unload_minutes": (3, int, 0, 120),  # arrêt de llama-server après inactivité
    "gpu_min_free_mb": (2600, int, 256, 24576),  # VRAM libre requise pour démarrer l'OCR
    "session_idle_minutes": (30, int, 1, 1440),
    "docx_latin_font": ("Arial", str, None, None),  # police latine par défaut du DOCX (modifiable par document)
    "docx_arabic_font": ("Simplified Arabic", str, None, None),  # police du texte arabe dans le DOCX
    "docx_columns": (True, bool, None, None),  # reproduire les pages en deux colonnes
    # --- Moteur OCR (Administration > Moteur OCR). Défaut : la valeur de backend/.env (OCR_...), voir ENV_DEFAULTS.
    "llama_cpp_dir": ("", str, None, None),  # dossier contenant llama-server.exe (build CUDA)
    "llama_cpp_cpu_dir": ("", str, None, None),  # build processeur de secours
    "llama_parallel": (1, int, 1, 8),  # blocs d'une page lus en parallèle (chaque slot prend de la VRAM)
    "llama_ngl": (99, int, 0, 999),  # couches sur le GPU (99 = toutes ; 0 = processeur seul)
    "llama_ctx_size": (16384, int, 4096, 65536),  # contexte par slot
    "cpu_fallback": (True, bool, None, None),  # sans GPU utilisable : OCR sur le processeur plutôt qu'un échec
    "page_width": (0, int, 0, 4000),  # largeur des pages données à Surya en pixels (0 = auto, 200 dpi)
    "block_token_ceiling": (8192, int, 1024, 32768),  # plafond de tokens générés par bloc
    "layout_refine": (True, bool, None, None),  # recaler la mise en page sur les lignes détectées
    "line_detector_device": ("cpu", str, None, None),  # cpu | cuda
    "stamp_erase": (True, bool, None, None),  # lire la page sans l'encre des tampons
    "layout_heron": (True, bool, None, None),  # second avis Heron sur la mise en page
    "second_reader": (True, bool, None, None),  # seconde lecture PaddleOCR-VL des blocs douteux
    "second_reader_threshold": (0.9, float, 0.0, 1.01),  # relire sous cette confiance
}
CHOICES = {"default_preprocess": {"auto", "leger", "delave", "tres_delave"}, "line_detector_device": {"cpu", "cuda"}}
PATH_KEYS = {"llama_cpp_dir", "llama_cpp_cpu_dir"}
PAGE_WIDTH_MIN = 1000

# Réglages dont la valeur par défaut vient de la configuration du poste (backend/.env) et non d'une constante :
# l'installation fixe les chemins, l'administrateur peut ensuite les changer sans toucher au fichier.
ENV_DEFAULTS: dict[str, Callable[[], Any]] = {
    "llama_cpp_dir": lambda: str(get_settings().llama_cpp_dir or ""),
    "llama_cpp_cpu_dir": lambda: str(get_settings().llama_cpp_cpu_dir or ""),
    "llama_parallel": lambda: get_settings().llama_parallel,
    "llama_ngl": lambda: get_settings().llama_ngl,
    "llama_ctx_size": lambda: get_settings().llama_ctx_size,
    "cpu_fallback": lambda: get_settings().cpu_fallback,
    "page_width": lambda: get_settings().page_width,
    "block_token_ceiling": lambda: get_settings().block_token_ceiling,
    "layout_refine": lambda: get_settings().layout_refine,
    "line_detector_device": lambda: get_settings().line_detector_device,
    "stamp_erase": lambda: get_settings().stamp_erase,
    "layout_heron": lambda: get_settings().layout_heron,
    "second_reader": lambda: get_settings().second_reader,
    "second_reader_threshold": lambda: get_settings().second_reader_threshold,
}


def default_value(key: str) -> Any:
    if key in ENV_DEFAULTS:
        try:
            return _coerce(key, ENV_DEFAULTS[key]())
        except SettingError:
            pass
    return DEFAULTS[key][0]


def clean_path(value: str) -> str:
    """Chemin collé depuis l'Explorateur : guillemets, espaces et séparateur final retirés."""
    v = value.strip().strip('"').strip("'").strip()
    return v.rstrip("\\/") if len(v) > 3 else v


class SettingError(ValueError):
    pass


def is_cloud_model(name: str) -> bool:
    return bool(CLOUD_RE.search(name or ""))


def _coerce(key: str, value: Any) -> Any:
    if key not in DEFAULTS:
        raise SettingError(f"Paramètre inconnu : {key}")
    _, typ, lo, hi = DEFAULTS[key]
    try:
        if typ is bool:
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "oui", "yes"}
            value = bool(value)
        elif typ is int:
            value = int(value)
        elif typ is float:
            value = float(value)
        else:
            value = clean_path(str(value)) if key in PATH_KEYS else str(value).strip()
    except (TypeError, ValueError) as e:
        raise SettingError(f"Valeur invalide pour {key}") from e
    if lo is not None and value < lo or hi is not None and value > hi:
        raise SettingError(f"{key} doit être entre {lo} et {hi}")
    if key in CHOICES and value not in CHOICES[key]:
        raise SettingError(f"{key} doit être l'une des valeurs : {', '.join(sorted(CHOICES[key]))}")
    if key == "page_width" and 0 < value < PAGE_WIDTH_MIN:
        raise SettingError(f"La largeur des pages doit être 0 (automatique) ou au moins {PAGE_WIDTH_MIN} pixels.")
    if key in PATH_KEYS and len(value) > 400:
        raise SettingError("Chemin trop long.")
    if key in ("docx_latin_font", "docx_arabic_font") and not value:
        raise SettingError("Le nom de police ne peut pas être vide.")
    if key == "ollama_model":
        if not value:
            raise SettingError("Le modèle de correction ne peut pas être vide (désactivez la correction par document).")
        if is_cloud_model(value):
            raise SettingError("Les modèles « cloud » sont refusés : ils envoient les documents sur Internet.")
    return value


def get_setting(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is not None:
        try:
            return _coerce(key, row.value)
        except SettingError:
            pass
    return default_value(key)


def get_all(db: Session) -> dict[str, Any]:
    stored = {r.key: r.value for r in db.query(Setting).all()}
    out = {}
    for key in DEFAULTS:
        try:
            out[key] = _coerce(key, stored[key]) if key in stored else default_value(key)
        except SettingError:
            out[key] = default_value(key)
    return out


def reset_setting(db: Session, key: str) -> Any:
    """Supprime la valeur enregistrée : le réglage reprend sa valeur par défaut (celle de backend/.env le cas échéant)."""
    if key not in DEFAULTS:
        raise SettingError(f"Paramètre inconnu : {key}")
    row = db.get(Setting, key)
    if row is not None:
        db.delete(row)
        db.flush()
    return default_value(key)


def set_setting(db: Session, key: str, value: Any) -> Any:
    value = _coerce(key, value)
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.flush()
    return value
