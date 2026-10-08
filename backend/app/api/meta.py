"""Options de formulaire et état du service pour tout utilisateur connecté (aucune donnée d'un autre utilisateur)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import admin
from app.api.deps import current_user
from app.api.documents import DOCX_FONTS
from app.core.db import get_db
from app.models import ACTIVE_STATUSES, DocStatus, Document, User
from app.services import app_settings
from app.worker.runner import read_status

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
        "default_preprocess": app_settings.get_setting(db, "default_preprocess"),
        "accepted": [".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"],
    }


@router.get("/status")
def service_status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Indicateur de l'interface : le worker tourne-t-il, combien de documents attendent (tous utilisateurs)."""
    w = read_status()
    queue = db.scalar(select(func.count()).select_from(Document).where(
        Document.status.in_([DocStatus.queued.value, DocStatus.waiting_gpu.value]))) or 0
    mine = db.scalar(select(func.count()).select_from(Document).where(
        Document.user_id == user.id, Document.status.in_(list(ACTIVE_STATUSES)))) or 0
    return {"worker_alive": bool(w and w.get("alive")), "worker_state": (w or {}).get("state", "unknown"),
            "engine": (w or {}).get("engine", "off"), "queue": queue, "my_active": mine}
