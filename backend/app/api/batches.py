"""Traitement par lot : plusieurs fichiers déposés ensemble, suivis, relancés, annulés et téléchargés ensemble.

Un lot n'est qu'un regroupement : chaque fichier reste un document ordinaire (même pipeline, même relecture).
"""
from __future__ import annotations

import logging
import zipfile
from collections import Counter

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.documents import (ACTIVE, cancel_document, delete_documents, doc_out, docx_name, enqueue,
                               retry_document)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import random_name
from app.models import Batch, DocStatus, Document, User, utcnow
from app.services import storage
from app.services.audit import audit

router = APIRouter(prefix="/batches", tags=["batches"])
log = logging.getLogger("ocr.batches")

GROUPS = {
    "queued": {DocStatus.queued.value, DocStatus.waiting_gpu.value},
    "running": {DocStatus.preprocessing.value, DocStatus.ocr.value, DocStatus.correcting.value,
                DocStatus.exporting.value},
    "review": {DocStatus.review.value},
    "done": {DocStatus.done.value},
    "failed": {DocStatus.failed.value},
    "cancelled": {DocStatus.cancelled.value},
}


class BatchIn(BaseModel):
    name: str = Field(default="", max_length=120)
    options: dict = Field(default_factory=dict)


def get_owned_batch(db: Session, user: User, batch_id: int) -> Batch:
    b = db.scalar(select(Batch).where(Batch.id == batch_id, Batch.user_id == user.id))
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot introuvable.")
    return b


def summary(docs: list[Document]) -> dict:
    """Compteurs, avancement pondéré par les pages et estimation du temps restant (débit mesuré sur ce lot)."""
    by_status = Counter(d.status for d in docs)
    counts = {g: sum(by_status.get(s, 0) for s in sts) for g, sts in GROUPS.items()}
    pages = sum(max(1, d.page_count) for d in docs)
    finished = {DocStatus.review.value, DocStatus.done.value, DocStatus.failed.value, DocStatus.cancelled.value}
    weighted = sum(max(1, d.page_count) * (1.0 if d.status in finished else d.progress) for d in docs)
    measured = [(d.finished_at or d.updated_at) - d.started_at for d in docs
                if d.started_at and d.status in (DocStatus.review.value, DocStatus.done.value)]
    measured_pages = sum(max(1, d.page_count) for d in docs
                         if d.started_at and d.status in (DocStatus.review.value, DocStatus.done.value))
    eta = None
    remaining = sum(max(1, d.page_count) * (1.0 - d.progress) for d in docs if d.status in ACTIVE)
    if measured and measured_pages and remaining > 0:
        per_page = sum(t.total_seconds() for t in measured) / measured_pages
        eta = int(per_page * remaining)
    return {"total": len(docs), "counts": counts, "pages": pages,
            "progress": round(weighted / pages, 4) if pages else 0.0, "active": counts["queued"] + counts["running"],
            "eta_seconds": eta, "exportable": sum(1 for d in docs if d.exports)}


def batch_out(b: Batch, with_docs: bool = False) -> dict:
    docs = list(b.documents)
    out = {"id": b.id, "name": b.name, "options": b.options, "created_at": b.created_at.isoformat(), **summary(docs)}
    if with_docs:
        out["documents"] = [doc_out(d) for d in docs]
    return out


@router.post("", status_code=201)
def create_batch(body: BatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    name = body.name.strip() or f"Lot du {utcnow():%d/%m/%Y %H:%M}"
    keep = {k: body.options[k] for k in ("preprocess", "llm", "review", "font", "llm_all", "ollama_model")
            if k in body.options}
    b = Batch(user_id=user.id, name=name[:120], options=keep)
    db.add(b)
    db.flush()
    audit(db, user.id, "batch_created", b.id)
    db.commit()
    return batch_out(b)


@router.get("")
def list_batches(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    base = select(Batch).where(Batch.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Batch.created_at.desc(), Batch.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [batch_out(b) for b in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/{batch_id}")
def get_batch(batch_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return batch_out(get_owned_batch(db, user, batch_id), with_docs=True)


@router.post("/{batch_id}/cancel")
def cancel_batch(batch_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    b = get_owned_batch(db, user, batch_id)
    n = sum(cancel_document(db, d) for d in b.documents)
    audit(db, user.id, "batch_cancel", b.id)
    db.commit()
    return {**batch_out(b, with_docs=True), "affected": n}


@router.post("/{batch_id}/retry")
def retry_batch(batch_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Relance les documents en échec ou annulés du lot."""
    b = get_owned_batch(db, user, batch_id)
    n = sum(retry_document(db, d) for d in b.documents)
    audit(db, user.id, "batch_retry", b.id)
    db.commit()
    return {**batch_out(b, with_docs=True), "affected": n}


@router.post("/{batch_id}/finalize")
def finalize_batch(batch_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Exporte en Word tous les documents du lot en attente de relecture (sans les relire un par un)."""
    b = get_owned_batch(db, user, batch_id)
    n = 0
    for d in b.documents:
        if d.status == DocStatus.review.value:
            d.status = DocStatus.exporting.value
            enqueue(db, d, "export")
            n += 1
    audit(db, user.id, "batch_finalize", b.id)
    db.commit()
    return {**batch_out(b, with_docs=True), "affected": n}


@router.delete("/{batch_id}", status_code=204)
def delete_batch(batch_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    """Supprime le lot et tous ses documents (fichiers compris). Refusé si l'un d'eux est en cours de traitement."""
    b = get_owned_batch(db, user, batch_id)
    delete_documents(db, user, list(b.documents))
    b = get_owned_batch(db, user, batch_id)
    db.delete(b)
    audit(db, user.id, "batch_deleted", batch_id)
    db.commit()


def _unique(name: str, used: set[str]) -> str:
    stem, ext = name.rsplit(".", 1)
    candidate, i = name, 2
    while candidate.lower() in used:
        candidate, i = f"{stem} ({i}).{ext}", i + 1
    used.add(candidate.lower())
    return candidate


@router.get("/{batch_id}/export.zip")
def download_zip(batch_id: int, background: BackgroundTasks, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Archive ZIP des Word déjà générés du lot (fichier temporaire sur disque, effacé après l'envoi)."""
    b = get_owned_batch(db, user, batch_id)
    files = []
    for d in b.documents:
        if d.exports:
            p = storage.resolve(d.exports[-1].path)
            if p.is_file():
                files.append((docx_name(d.original_filename), p))
    if not files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucun document Word disponible dans ce lot.")
    tmp_dir = get_settings().data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / random_name(".zip")
    used: set[str] = set()
    try:
        # Word est déjà compressé : stockage simple, rapide même pour des centaines de fichiers.
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:
            for name, p in files:
                z.write(p, _unique(name, used))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    background.add_task(tmp.unlink, missing_ok=True)
    audit(db, user.id, "batch_download", b.id)
    db.commit()
    safe = "".join(c if c.isalnum() or c in " -_()" else "_" for c in b.name).strip() or "lot"
    return FileResponse(tmp, filename=f"{safe}.zip", media_type="application/zip",
                        headers={"Cache-Control": "private, no-store"})
