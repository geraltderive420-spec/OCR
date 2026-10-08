"""Journal d'audit : actions et identifiants seulement, jamais de contenu de document."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog

ACTIONS = {
    "login", "login_failed", "logout", "password_changed", "upload", "download", "delete", "retry",
    "finalize", "user_created", "user_updated", "password_reset", "setting_changed", "purge",
    "cancel", "setting_reset", "batch_created", "batch_cancel", "batch_retry", "batch_finalize", "batch_deleted",
    "batch_download",
}


def audit(db: Session, user_id: int | None, action: str, target: str | int | None = None) -> None:
    assert action in ACTIONS, action
    db.add(AuditLog(user_id=user_id, action=action, target=None if target is None else str(target)[:64]))
