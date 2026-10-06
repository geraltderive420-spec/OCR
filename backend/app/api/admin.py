"""Administration : utilisateurs, statistiques (sans contenu), paramètres, état des services (PLAN.md §10)."""
from __future__ import annotations

import shutil

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import admin_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import check_password_strength, hash_password
from app.models import AuditLog, DocStatus, Document, Role, User, utcnow
from app.services import app_settings
from app.services.audit import audit
from app.services.gpu import free_vram_mb
from app.services.storage import user_usage_bytes

router = APIRouter(prefix="/admin", tags=["admin"])


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(max_length=256)
    role: str = "user"


class UserPatch(BaseModel):
    is_active: bool | None = None
    role: str | None = None


class PasswordReset(BaseModel):
    password: str = Field(max_length=256)


def user_row(u: User, db: Session) -> dict:
    n_docs = db.scalar(select(func.count()).select_from(Document).where(Document.user_id == u.id)) or 0
    return {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active,
            "must_change_password": u.must_change_password, "created_at": u.created_at.isoformat(),
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "documents": n_docs, "usage_bytes": user_usage_bytes(u.id)}  # métadonnées seulement


@router.get("/users")
def list_users(admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> list[dict]:
    return [user_row(u, db) for u in db.scalars(select(User).order_by(User.username)).all()]


@router.post("/users", status_code=201)
def create_user(body: UserIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    if body.role not in (Role.admin.value, Role.user.value):
        raise HTTPException(422, "Rôle invalide.")
    problem = check_password_strength(body.password)
    if problem:
        raise HTTPException(422, problem)
    username = body.username.lower()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Cet identifiant existe déjà.")
    u = User(username=username, password_hash=hash_password(body.password), role=body.role, must_change_password=True)
    db.add(u)
    db.flush()
    audit(db, admin.id, "user_created", u.id)
    db.commit()
    return user_row(u, db)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Utilisateur introuvable.")
    if u.id == admin.id and (body.is_active is False or (body.role and body.role != Role.admin.value)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Vous ne pouvez pas retirer vos propres droits ou vous désactiver.")
    if body.role is not None:
        if body.role not in (Role.admin.value, Role.user.value):
            raise HTTPException(422, "Rôle invalide.")
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
        if not body.is_active:
            u.token_version += 1  # coupe les sessions ouvertes
    audit(db, admin.id, "user_updated", u.id)
    db.commit()
    return user_row(u, db)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordReset, admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Utilisateur introuvable.")
    problem = check_password_strength(body.password)
    if problem:
        raise HTTPException(422, problem)
    u.password_hash = hash_password(body.password)
    u.must_change_password = True
    u.failed_attempts, u.locked_until = 0, None
    u.token_version += 1
    audit(db, admin.id, "password_reset", u.id)
    db.commit()
    return user_row(u, db)


@router.get("/stats")
def stats(admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    by_status = dict(db.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    pages = db.scalar(select(func.coalesce(func.sum(Document.page_count), 0))) or 0
    return {"users": db.scalar(select(func.count()).select_from(User)) or 0,
            "documents": sum(by_status.values()), "documents_par_statut": by_status, "pages_total": int(pages),
            "audit_recent": [{"action": a.action, "user_id": a.user_id, "target": a.target, "at": a.created_at.isoformat()}
                             for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(50))]}


@router.get("/settings")
def get_settings_all(admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    return app_settings.get_all(db)


@router.put("/settings")
def put_settings(body: dict, admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    try:
        for k, v in body.items():
            app_settings.set_setting(db, k, v)
            audit(db, admin.id, "setting_changed", k)
    except app_settings.SettingError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    db.commit()
    return app_settings.get_all(db)


def ollama_tags() -> list[dict] | None:
    try:
        r = httpx.get(f"{get_settings().ollama_url}/api/tags", timeout=5)
        r.raise_for_status()
        return r.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None


@router.get("/ollama/models")
def ollama_models(admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    """Modèles Ollama utilisables pour la correction : locaux seulement (les modèles « cloud » sont exclus)."""
    tags = ollama_tags()
    current = app_settings.get_setting(db, "ollama_model")
    if tags is None:
        return {"available": False, "models": [], "current": current}
    models = []
    for m in tags:
        name = m.get("name") or m.get("model")
        if not name or app_settings.is_cloud_model(name) or m.get("remote_host") or m.get("remote_model"):
            continue
        models.append({"name": name, "size_bytes": m.get("size", 0)})
    return {"available": True, "models": models, "current": current}


@router.get("/services")
def services(admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    tags = ollama_tags()
    du = shutil.disk_usage(s.data_dir if s.data_dir.exists() else s.data_dir.parent)
    llama = None
    try:
        llama = httpx.get(f"http://127.0.0.1:{s.llama_port}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        llama = False
    return {"base_de_donnees": True, "ollama": tags is not None, "llama_server": llama,
            "vram_libre_mo": free_vram_mb(), "disque_libre_mo": du.free // (1024 * 1024),
            "disque_total_mo": du.total // (1024 * 1024), "ocr_backend": s.ocr_backend,
            "file_attente": db.scalar(select(func.count()).select_from(Document).where(
                Document.status.in_([DocStatus.queued.value, DocStatus.waiting_gpu.value]))) or 0}
