"""Hachage des mots de passe (argon2) et jetons JWT."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_ph = PasswordHasher()
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def check_password_strength(password: str) -> str | None:
    """Retourne un message d'erreur, ou None si le mot de passe est acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
    if password.isalpha() or password.isdigit():
        return "Le mot de passe doit mélanger lettres et chiffres ou symboles."
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_token(user_id: int, kind: str, minutes: int, version: int = 0) -> str:
    payload = {"sub": str(user_id), "kind": kind, "v": version, "jti": uuid.uuid4().hex,
               "exp": _now() + timedelta(minutes=minutes), "iat": _now()}
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def decode_token(token: str, kind: str) -> tuple[int, int] | None:
    """Retourne (identifiant utilisateur, version) si le jeton est valide et du bon type, sinon None."""
    try:
        data = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if data.get("kind") != kind:
        return None
    try:
        return int(data["sub"]), int(data.get("v", 0))
    except (KeyError, ValueError):
        return None


def random_name(suffix: str = "") -> str:
    """Nom de fichier aléatoire : le nom d'origine n'est jamais utilisé sur disque."""
    return secrets.token_hex(16) + suffix
