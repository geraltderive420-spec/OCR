"""Options de formulaire pour tout utilisateur connecté : modèles Ollama locaux, limites d'upload."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import admin
from app.api.deps import current_user
from app.api.documents import DOCX_FONTS
from app.core.db import get_db
from app.models import User
from app.services import app_settings

router = APIRouter(tags=["meta"])


@router.get("/options")
def options(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    tags = admin.ollama_tags()
    models = []
    for m in tags or []:
        name = m.get("name") or m.get("model")
        if name and not app_settings.is_cloud_model(name) and not m.get("remote_host") and not m.get("remote_model"):
            models.append(name)
    return {
        "preprocess": ["auto", "leger", "delave", "tres_delave"],
        "llm_modes": ["off", "auto", "validate"],
        "ollama_available": tags is not None,
        "ollama_models": models,
        "default_model": app_settings.get_setting(db, "ollama_model"),
        "max_upload_mb": app_settings.get_setting(db, "max_upload_mb"),
        "max_pages": app_settings.get_setting(db, "max_pages"),
        "fonts": sorted(DOCX_FONTS),
        "default_font": app_settings.get_setting(db, "docx_latin_font"),
    }
