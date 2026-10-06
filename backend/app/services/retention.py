"""Rétention : purge automatique des documents anciens (fichiers, pages, découpes, exports, lignes en base)."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocStatus, Document, utcnow
from app.services import app_settings, storage
from app.services.audit import audit

log = logging.getLogger("ocr.retention")
ACTIVE = {DocStatus.queued.value, DocStatus.waiting_gpu.value, DocStatus.preprocessing.value,
          DocStatus.ocr.value, DocStatus.correcting.value, DocStatus.exporting.value}


def purge_expired(db: Session) -> int:
    days = app_settings.get_setting(db, "retention_days")
    limit = utcnow() - timedelta(days=days)
    old = db.scalars(select(Document).where(Document.created_at < limit, Document.status.notin_(ACTIVE))).all()
    n = 0
    for doc in old:
        uid, did = doc.user_id, doc.id
        db.delete(doc)
        audit(db, None, "purge", did)
        db.commit()
        storage.delete_document_files(uid, did)
        n += 1
    if n:
        log.info("purge : %s document(s) supprimé(s)", n)
    return n
