from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_token
from app.models import Role, User


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return None


def get_user_allow_password_change(request: Request, db: Session = Depends(get_db)) -> User:
    token = _bearer(request)
    decoded = decode_token(token, "access") if token else None
    if decoded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée ou invalide.")
    user_id, version = decoded
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée ou invalide.")
    return user


def current_user(user: User = Depends(get_user_allow_password_change)) -> User:
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password_change_required")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != Role.admin.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé à l'administrateur.")
    return user
