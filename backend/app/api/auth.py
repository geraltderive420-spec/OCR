from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_user_allow_password_change
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import (check_password_strength, create_token, decode_token, hash_password, verify_password)
from app.models import User, utcnow
from app.services import app_settings
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE = "ocr_refresh"
_DUMMY_HASH = hash_password("mot-de-passe-factice-pour-egaliser-le-temps")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    old_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


def user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "must_change_password": u.must_change_password}


def _issue(response: Response, db: Session, user: User) -> dict:
    s = get_settings()
    idle = app_settings.get_setting(db, "session_idle_minutes")
    access = create_token(user.id, "access", s.access_token_minutes, user.token_version)
    refresh = create_token(user.id, "refresh", idle, user.token_version)
    response.set_cookie(COOKIE, refresh, max_age=idle * 60, httponly=True, samesite="strict",
                        secure=s.cookie_secure, path="/api/auth")
    return {"access_token": access, "token_type": "bearer", "user": user_out(user)}


@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiant ou mot de passe incorrect.")
    if user is None:
        verify_password(body.password, _DUMMY_HASH)  # temps de réponse comparable
        raise generic
    now = utcnow()
    if user.locked_until and user.locked_until > now:
        audit(db, user.id, "login_failed", "locked")
        db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Compte temporairement verrouillé. Réessayez plus tard.")
    if not user.is_active or not verify_password(body.password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= s.login_max_failures:
            user.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
            user.failed_attempts = 0
        audit(db, user.id, "login_failed")
        db.commit()
        raise generic
    user.failed_attempts, user.locked_until, user.last_login_at = 0, None, now
    audit(db, user.id, "login")
    db.commit()
    return _issue(response, db, user)


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE)
    decoded = decode_token(token, "refresh") if token else None
    if decoded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée.")
    user = db.get(User, decoded[0])
    if user is None or not user.is_active or user.token_version != decoded[1]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée.")
    return _issue(response, db, user)  # fenêtre glissante : chaque rafraîchissement prolonge la session


@router.post("/logout", status_code=204)
def logout(response: Response, request: Request, db: Session = Depends(get_db)) -> Response:
    token = request.cookies.get(COOKIE)
    decoded = decode_token(token, "refresh") if token else None
    if decoded:
        user = db.get(User, decoded[0])
        if user and user.token_version == decoded[1]:
            user.token_version += 1
            audit(db, user.id, "logout")
            db.commit()
    response.delete_cookie(COOKIE, path="/api/auth")
    response.status_code = 204
    return response


@router.post("/change-password")
def change_password(body: PasswordIn, response: Response, user: User = Depends(get_user_allow_password_change),
                    db: Session = Depends(get_db)) -> dict:
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mot de passe actuel incorrect.")
    problem = check_password_strength(body.new_password)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    if body.new_password == body.old_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Le nouveau mot de passe doit être différent.")
    user = db.merge(user)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version += 1
    audit(db, user.id, "password_changed")
    db.commit()
    return _issue(response, db, user)


@router.get("/me")
def me(user: User = Depends(get_user_allow_password_change)) -> dict:
    return user_out(user)
