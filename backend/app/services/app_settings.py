"""Paramètres modifiables par l'administrateur (table `settings`), avec valeurs par défaut et validation."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

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
}
CHOICES = {"default_preprocess": {"auto", "leger", "delave", "tres_delave"}}


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
            value = str(value).strip()
    except (TypeError, ValueError) as e:
        raise SettingError(f"Valeur invalide pour {key}") from e
    if lo is not None and value < lo or hi is not None and value > hi:
        raise SettingError(f"{key} doit être entre {lo} et {hi}")
    if key in CHOICES and value not in CHOICES[key]:
        raise SettingError(f"{key} doit être l'une des valeurs : {', '.join(sorted(CHOICES[key]))}")
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
    return DEFAULTS[key][0]


def get_all(db: Session) -> dict[str, Any]:
    stored = {r.key: r.value for r in db.query(Setting).all()}
    out = {}
    for key, (default, *_rest) in DEFAULTS.items():
        try:
            out[key] = _coerce(key, stored[key]) if key in stored else default
        except SettingError:
            out[key] = default
    return out


def set_setting(db: Session, key: str, value: Any) -> Any:
    value = _coerce(key, value)
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.flush()
    return value
