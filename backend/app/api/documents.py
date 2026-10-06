from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.security import random_name
from app.models import (Block, Correction, CorrectionStatus, DocStatus, Document, Job, JobStatus, Page, User, utcnow)
from app.services import app_settings, pipeline, storage
from app.services.audit import audit
from app.services.ocr.html_utils import direction_of, sanitize_html, to_text

router = APIRouter(tags=["documents"])
PDF_MAGIC = b"%PDF-"
VALID_PREPROCESS = {"auto", "leger", "delave", "tres_delave"}
VALID_LLM = {"off", "auto", "validate"}
ACTIVE = {DocStatus.queued.value, DocStatus.waiting_gpu.value, DocStatus.preprocessing.value, DocStatus.ocr.value,
          DocStatus.correcting.value, DocStatus.exporting.value}


def safe_filename(name: str) -> str:
    """Nom d'origine assaini (affichage seulement : il n'est jamais utilisé comme chemin)."""
    name = Path(name or "document.pdf").name
    name = re.sub(r"[^\w.\- ()À-ɏ؀-ۿ]", "_", name)[:120].strip(" .")
    return name or "document.pdf"


def get_owned(db: Session, user: User, document_id: int) -> Document:
    """Toute requête sur un document filtre sur user_id : un autre utilisateur obtient 404, jamais 403."""
    doc = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.id))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document introuvable.")
    return doc


def doc_out(d: Document) -> dict:
    return {"id": d.id, "filename": d.original_filename, "status": d.status, "progress": d.progress,
            "page_count": d.page_count, "size_bytes": d.size_bytes, "options": d.options, "error": d.error,
            "created_at": d.created_at.isoformat(), "finished_at": d.finished_at.isoformat() if d.finished_at else None,
            "has_export": bool(d.exports)}


def enqueue(db: Session, doc: Document, kind: str = "process") -> None:
    db.add(Job(document_id=doc.id, type=kind, status=JobStatus.queued.value))


@router.post("/documents", status_code=201)
def upload(file: UploadFile = File(...), preprocess: str = Form("auto"), llm: str = Form("off"),
           review: bool = Form(False), ollama_model: str | None = Form(None), llm_all: bool = Form(False),
           user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if preprocess not in VALID_PREPROCESS or llm not in VALID_LLM:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Options invalides.")
    if ollama_model:
        try:
            app_settings._coerce("ollama_model", ollama_model)  # refuse les modèles « cloud »
        except app_settings.SettingError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    max_mb = app_settings.get_setting(db, "max_upload_mb")
    quota = app_settings.get_setting(db, "quota_mb_per_user") * 1024 * 1024
    if storage.user_usage_bytes(user.id) >= quota:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Quota d'espace atteint : supprimez d'anciens documents.")

    # Flux vers un fichier temporaire sous data/, plafonné : jamais le fichier entier en mémoire.
    tmp_dir = storage.doc_dir("uploads", user.id, 0, create=True)
    tmp = tmp_dir / (random_name(".part"))
    size, head = 0, b""
    try:
        with open(tmp, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                if not head:
                    head = chunk[:8]
                size += len(chunk)
                if size > max_mb * 1024 * 1024:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Fichier trop volumineux (max {max_mb} Mo).")
                out.write(chunk)
        if not head.startswith(PDF_MAGIC):  # type réel, pas l'extension ni le Content-Type
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Le fichier n'est pas un PDF.")
        try:
            pages = pipeline.count_pdf_pages(tmp)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "PDF illisible ou protégé.") from e
        max_pages = app_settings.get_setting(db, "max_pages")
        if pages < 1 or pages > max_pages:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Nombre de pages invalide (max {max_pages}).")
        doc = Document(user_id=user.id, original_filename=safe_filename(file.filename or ""), stored_name="",
                       size_bytes=size, page_count=pages,
                       options={"preprocess": preprocess, "llm": llm, "review": review, "llm_all": llm_all,
                                "ollama_model": ollama_model or None})
        db.add(doc)
        db.flush()
        dest, _ = storage.new_path("uploads", user.id, doc.id, ".pdf")
        tmp.replace(dest)
        doc.stored_name = dest.name
        enqueue(db, doc)
        audit(db, user.id, "upload", doc.id)
        db.commit()
    finally:
        tmp.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
    return doc_out(doc)


@router.get("/documents")
def list_documents(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    base = select(Document).where(Document.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Document.created_at.desc(), Document.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [doc_out(d) for d in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/documents/{document_id}")
def get_document(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    doc = get_owned(db, user, document_id)
    out = doc_out(doc)
    out["pages"] = [{"index": p.index, "status": p.status, "mean_confidence": p.mean_confidence} for p in doc.pages]
    out["pending_corrections"] = db.scalar(
        select(func.count()).select_from(Correction).join(Block).join(Page).where(
            Page.document_id == doc.id, Correction.status == CorrectionStatus.proposed.value)) or 0
    return out


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    doc = get_owned(db, user, document_id)
    uid, did = doc.user_id, doc.id
    db.delete(doc)  # lignes en cascade : pages, blocs, corrections, jobs, exports
    audit(db, user.id, "delete", did)
    db.commit()
    storage.delete_document_files(uid, did)  # effacement réel des fichiers


@router.post("/documents/{document_id}/retry")
def retry(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    doc = get_owned(db, user, document_id)
    if doc.status != DocStatus.failed.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seul un document en échec peut être relancé.")
    for p in doc.pages:
        if p.status == "error":
            p.status, p.error = "pending", None
    doc.status, doc.error, doc.progress = DocStatus.queued.value, None, 0.0
    enqueue(db, doc)
    audit(db, user.id, "retry", doc.id)
    db.commit()
    return doc_out(doc)


# ------------------------------------------------------------------ relecture

def block_out(b: Block) -> dict:
    return {"id": b.id, "order": b.reading_order, "label": b.label, "bbox": b.bbox, "html": b.html_current,
            "html_ocr": b.html_ocr, "confidence": b.confidence, "direction": b.direction, "to_check": b.to_check,
            "error": b.error, "has_crop": bool(b.crop_path),
            "corrections": [{"id": c.id, "source": c.source, "before": c.before, "after": c.after, "status": c.status,
                             "model": c.model_name} for c in b.corrections if c.source == "llm"]}


@router.get("/documents/{document_id}/pages/{n}")
def get_page(document_id: int, n: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    doc = get_owned(db, user, document_id)
    page = next((p for p in doc.pages if p.index == n), None)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page introuvable.")
    return {"index": page.index, "width": page.width, "height": page.height, "status": page.status,
            "image_url": f"/api/documents/{doc.id}/pages/{n}/image" if page.image_path else None,
            "blocks": [block_out(b) for b in page.blocks]}


def _owned_file(db: Session, user: User, rel: str | None) -> Path:
    if not rel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fichier introuvable.")
    p = storage.resolve(rel)
    if not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fichier introuvable.")
    return p


@router.get("/documents/{document_id}/pages/{n}/image")
def page_image(document_id: int, n: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_owned(db, user, document_id)
    page = next((p for p in doc.pages if p.index == n), None)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page introuvable.")
    return FileResponse(_owned_file(db, user, page.image_path), media_type="image/jpeg",
                        headers={"Cache-Control": "private, no-store"})


@router.get("/blocks/{block_id}/crop")
def block_crop(block_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = _owned_block(db, user, block_id)
    return FileResponse(_owned_file(db, user, b.crop_path), media_type="image/png", headers={"Cache-Control": "private, no-store"})


def _owned_block(db: Session, user: User, block_id: int) -> Block:
    b = db.scalar(select(Block).join(Page).join(Document).where(Block.id == block_id, Document.user_id == user.id))
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bloc introuvable.")
    return b


def _owned_correction(db: Session, user: User, cid: int) -> Correction:
    c = db.scalar(select(Correction).join(Block).join(Page).join(Document).where(Correction.id == cid, Document.user_id == user.id))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Correction introuvable.")
    return c


class BlockPatch(BaseModel):
    html: str | None = None
    to_check: bool | None = None


@router.patch("/blocks/{block_id}")
def patch_block(block_id: int, body: BlockPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    b = _owned_block(db, user, block_id)
    if b.page.document.status not in (DocStatus.review.value, DocStatus.done.value):
        raise HTTPException(status.HTTP_409_CONFLICT, "Le document n'est pas modifiable maintenant.")
    if body.html is not None:
        new = sanitize_html(body.html)
        if len(new) > 200_000:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Texte trop long.")
        if new != b.html_current:
            db.add(Correction(block_id=b.id, source="user", before=b.html_current, after=new, status=CorrectionStatus.accepted.value))
            b.html_current = new
            b.direction = direction_of(to_text(new))
        b.to_check = False
    if body.to_check is not None:
        b.to_check = body.to_check
    db.commit()
    return block_out(b)


@router.post("/corrections/{correction_id}/accept")
def accept_correction(correction_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    c = _owned_correction(db, user, correction_id)
    if c.status == CorrectionStatus.proposed.value:
        c.block.html_current = c.after
        c.status = CorrectionStatus.accepted.value
    elif c.status == CorrectionStatus.reverted.value:
        c.block.html_current = c.after
        c.status = CorrectionStatus.accepted.value
    db.commit()
    return block_out(c.block)


@router.post("/corrections/{correction_id}/reject")
def reject_correction(correction_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Refuse une proposition, ou annule une correction appliquée automatiquement (retour au texte d'avant)."""
    c = _owned_correction(db, user, correction_id)
    if c.status == CorrectionStatus.auto_applied.value or c.status == CorrectionStatus.accepted.value:
        c.block.html_current = c.before
        c.status = CorrectionStatus.reverted.value
    elif c.status == CorrectionStatus.proposed.value:
        c.status = CorrectionStatus.rejected.value
    db.commit()
    return block_out(c.block)


@router.post("/documents/{document_id}/finalize")
def finalize(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    doc = get_owned(db, user, document_id)
    if doc.status not in (DocStatus.review.value, DocStatus.done.value):
        raise HTTPException(status.HTTP_409_CONFLICT, "Le document n'est pas prêt à être finalisé.")
    doc.status = DocStatus.exporting.value
    enqueue(db, doc, "export")
    audit(db, user.id, "finalize", doc.id)
    db.commit()
    return doc_out(doc)


@router.get("/documents/{document_id}/export.docx")
def download(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_owned(db, user, document_id)
    exp = doc.exports[-1] if doc.exports else None
    if exp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucun export disponible.")
    audit(db, user.id, "download", doc.id)
    db.commit()
    stem = re.sub(r"\.pdf$", "", doc.original_filename, flags=re.IGNORECASE) or "document"
    return FileResponse(_owned_file(db, user, exp.path), filename=f"{stem}.docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Cache-Control": "private, no-store"})
