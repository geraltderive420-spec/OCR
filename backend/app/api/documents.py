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
from app.models import (ACTIVE_STATUSES, BUSY_STATUSES, Batch, Block, Correction, CorrectionStatus, DocStatus,
                        Document, Job, JobStatus, Page, User)
from app.services import app_settings, document_model, pipeline, storage
from app.services.audit import audit
from app.services.ocr.html_utils import direction_of, sanitize_html, to_text

router = APIRouter(tags=["documents"])
PDF_MAGIC = b"%PDF-"
# Images acceptées (converties en PDF à l'arrivée) : JPEG, PNG, TIFF (multipage).
IMAGE_MAGICS = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"II*\x00", b"MM\x00*")
VALID_PREPROCESS = {"auto", "leger", "delave", "tres_delave"}
# Police latine du Word, au choix pour chaque document (la famille ne se détecte pas de façon fiable sur un scan).
DOCX_FONTS = {"Arial", "Times New Roman", "Calibri", "Cambria", "Verdana", "Tahoma"}
VALID_LLM = {"off", "auto", "validate"}
BUSY = set(BUSY_STATUSES)
ACTIVE = set(ACTIVE_STATUSES)
# Filtres de la liste des documents (groupes de statuts).
STATUS_GROUPS = {
    "active": ACTIVE, "review": {DocStatus.review.value}, "done": {DocStatus.done.value},
    "failed": {DocStatus.failed.value}, "cancelled": {DocStatus.cancelled.value},
}
SOURCE_EXT = re.compile(r"\.(pdf|jpe?g|png|tiff?)$", re.IGNORECASE)


def safe_filename(name: str) -> str:
    """Nom d'origine assaini (affichage seulement : il n'est jamais utilisé comme chemin)."""
    name = Path(name or "document.pdf").name
    name = re.sub(r"[^\w.\- ()À-ɏ؀-ۿ]", "_", name)[:120].strip(" .")
    return name or "document.pdf"


def docx_name(original: str) -> str:
    return (SOURCE_EXT.sub("", original) or "document") + ".docx"


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
            "started_at": d.started_at.isoformat() if d.started_at else None,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            "batch_id": d.batch_id, "batch_name": d.batch.name if d.batch_id and d.batch else None,
            "cancel_requested": bool(d.cancel_requested), "has_export": bool(d.exports)}


def enqueue(db: Session, doc: Document, kind: str = "process") -> None:
    db.add(Job(document_id=doc.id, type=kind, status=JobStatus.queued.value))


def image_to_pdf(src: Path, dest: Path) -> int:
    """Image (JPEG, PNG, TIFF multipage) -> PDF d'images à sa résolution d'origine. Retourne le nombre de pages."""
    from PIL import Image, ImageOps, ImageSequence

    frames = []
    with Image.open(src) as im:
        dpi = im.info.get("dpi", (200, 200))[0] or 200
        for frame in ImageSequence.Iterator(im):
            f = ImageOps.exif_transpose(frame)  # photo prise de travers : orientation EXIF appliquée
            frames.append(f.convert("RGB") if f.mode not in ("RGB", "L") else f.copy())
    if not frames:
        raise ValueError("image vide")
    dpi = float(min(1200, max(72, dpi)))
    frames[0].save(dest, format="PDF", save_all=True, append_images=frames[1:], resolution=dpi)
    return len(frames)


@router.post("/documents", status_code=201)
def upload(file: UploadFile = File(...), preprocess: str = Form("auto"), llm: str = Form("off"),
           review: bool = Form(False), ollama_model: str | None = Form(None), llm_all: bool = Form(False),
           font: str = Form(""), batch_id: int | None = Form(None),
           user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if preprocess not in VALID_PREPROCESS or llm not in VALID_LLM or (font and font not in DOCX_FONTS):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Options invalides.")
    if ollama_model:
        try:
            app_settings._coerce("ollama_model", ollama_model)  # refuse les modèles « cloud »
        except app_settings.SettingError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    batch = None
    if batch_id is not None:
        batch = db.scalar(select(Batch).where(Batch.id == batch_id, Batch.user_id == user.id))
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot introuvable.")
    max_mb = app_settings.get_setting(db, "max_upload_mb")
    quota = app_settings.get_setting(db, "quota_mb_per_user") * 1024 * 1024
    if storage.user_usage_bytes(user.id) >= quota:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Quota d'espace atteint : supprimez d'anciens documents.")

    # Flux vers un fichier temporaire sous data/, plafonné : jamais le fichier entier en mémoire.
    tmp_dir = storage.doc_dir("uploads", user.id, 0, create=True)
    tmp = tmp_dir / (random_name(".part"))
    converted = tmp_dir / (random_name(".pdf"))
    size, head = 0, b""
    try:
        with open(tmp, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                if len(head) < 16:
                    head += chunk[:16]
                size += len(chunk)
                if size > max_mb * 1024 * 1024:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Fichier trop volumineux (max {max_mb} Mo).")
                out.write(chunk)
        source = tmp
        if head.startswith(PDF_MAGIC):  # type réel, pas l'extension ni le Content-Type
            try:
                pages = pipeline.count_pdf_pages(tmp)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "PDF illisible ou protégé.") from e
        elif any(head.startswith(m) for m in IMAGE_MAGICS):
            try:
                pages = image_to_pdf(tmp, converted)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Image illisible.") from e
            source = converted
        else:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Format non pris en charge : PDF, JPEG, PNG ou TIFF.")
        max_pages = app_settings.get_setting(db, "max_pages")
        if pages < 1 or pages > max_pages:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Nombre de pages invalide (max {max_pages}).")
        doc = Document(user_id=user.id, batch_id=batch.id if batch else None,
                       original_filename=safe_filename(file.filename or ""), stored_name="",
                       size_bytes=size, page_count=pages,
                       options={"preprocess": preprocess, "llm": llm, "review": review, "llm_all": llm_all,
                                "ollama_model": ollama_model or None, "font": font or None})
        db.add(doc)
        db.flush()
        dest, _ = storage.new_path("uploads", user.id, doc.id, ".pdf")
        source.replace(dest)
        doc.stored_name = dest.name
        enqueue(db, doc)
        audit(db, user.id, "upload", doc.id)
        db.commit()
    finally:
        tmp.unlink(missing_ok=True)
        converted.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
    return doc_out(doc)


@router.get("/documents")
def list_documents(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   q: str | None = Query(None, max_length=100), status_: str | None = Query(None, alias="status"),
                   batch_id: int | None = Query(None),
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    base = select(Document).where(Document.user_id == user.id)
    if q and q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        base = base.where(Document.original_filename.ilike(like, escape="\\"))
    if status_:
        wanted: set[str] = set()
        for part in status_.split(","):
            wanted |= STATUS_GROUPS.get(part.strip(), {part.strip()})
        base = base.where(Document.status.in_(wanted))
    if batch_id is not None:
        base = base.where(Document.batch_id == batch_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Document.created_at.desc(), Document.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    counts = dict(db.execute(select(Document.status, func.count()).where(Document.user_id == user.id)
                             .group_by(Document.status)).all())
    return {"items": [doc_out(d) for d in rows], "total": total, "page": page, "page_size": page_size,
            "counts": {g: sum(counts.get(s, 0) for s in sts) for g, sts in STATUS_GROUPS.items()}}


@router.get("/documents/{document_id}")
def get_document(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    doc = get_owned(db, user, document_id)
    out = doc_out(doc)
    out["pages"] = [{"index": p.index, "status": p.status, "mean_confidence": p.mean_confidence} for p in doc.pages]
    out["pending_corrections"] = db.scalar(
        select(func.count()).select_from(Correction).join(Block).join(Page).where(
            Page.document_id == doc.id, Correction.status == CorrectionStatus.proposed.value)) or 0
    out["to_check"] = db.scalar(select(func.count()).select_from(Block).join(Page).where(
        Page.document_id == doc.id, Block.to_check.is_(True))) or 0
    out["queue_position"] = queue_position(db, doc)
    return out


def queue_position(db: Session, doc: Document) -> int | None:
    """Rang approximatif dans la file (1 = prochain), pour un document en attente."""
    if doc.status not in (DocStatus.queued.value, DocStatus.waiting_gpu.value):
        return None
    mine = db.scalar(select(func.min(Job.id)).where(Job.document_id == doc.id, Job.status == JobStatus.queued.value))
    if mine is None:
        return None
    return (db.scalar(select(func.count()).select_from(Job).where(
        Job.status.in_([JobStatus.queued.value, JobStatus.running.value]), Job.id < mine)) or 0) + 1


def delete_documents(db: Session, user: User, docs: list[Document]) -> int:
    """Supprime des documents (lignes en cascade, puis fichiers sur disque). Refuse si l'un est en traitement."""
    if any(d.status in BUSY for d in docs):
        raise HTTPException(status.HTTP_409_CONFLICT, "Un document est en cours de traitement : réessayez dans un instant.")
    ids = [(d.user_id, d.id) for d in docs]
    for d in docs:
        db.delete(d)  # lignes en cascade : pages, blocs, corrections, jobs, exports
        audit(db, user.id, "delete", d.id)
    db.commit()
    for uid, did in ids:
        storage.delete_document_files(uid, did)  # effacement réel des fichiers
    return len(ids)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    doc = get_owned(db, user, document_id)
    if doc.status in BUSY:
        raise HTTPException(status.HTTP_409_CONFLICT, "Le document est en cours de traitement : réessayez dans un instant.")
    delete_documents(db, user, [doc])


def cancel_document(db: Session, doc: Document) -> bool:
    """Annule un document en attente tout de suite ; en cours de traitement, le worker s'arrête à la page suivante."""
    if doc.status not in ACTIVE or doc.status == DocStatus.exporting.value:
        return False
    doc.cancel_requested = True
    if doc.status in (DocStatus.queued.value, DocStatus.waiting_gpu.value):
        for j in doc.jobs:
            if j.status == JobStatus.queued.value:
                j.status = JobStatus.done.value
        doc.status, doc.error = DocStatus.cancelled.value, None
    return True


def retry_document(db: Session, doc: Document) -> bool:
    if doc.status not in (DocStatus.failed.value, DocStatus.cancelled.value):
        return False
    for p in doc.pages:
        if p.status == "error":
            p.status, p.error = "pending", None
        elif p.status == "rendered" and not (p.preprocessed_path and storage.resolve(p.preprocessed_path).is_file()):
            p.status = "pending"  # image de travail effacée à l'annulation : la page sera rendue à nouveau
    doc.status, doc.error, doc.progress = DocStatus.queued.value, None, 0.0
    doc.cancel_requested, doc.started_at = False, None
    enqueue(db, doc)
    return True


@router.post("/documents/{document_id}/cancel")
def cancel(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    doc = get_owned(db, user, document_id)
    if not cancel_document(db, doc):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce document n'est pas en cours de traitement.")
    audit(db, user.id, "cancel", doc.id)
    db.commit()
    return doc_out(doc)


@router.post("/documents/{document_id}/retry")
def retry(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    doc = get_owned(db, user, document_id)
    if not retry_document(db, doc):
        raise HTTPException(status.HTTP_409_CONFLICT, "Seul un document en échec ou annulé peut être relancé.")
    audit(db, user.id, "retry", doc.id)
    db.commit()
    return doc_out(doc)


# ------------------------------------------------------------------ relecture

def block_out(b: Block) -> dict:
    # Le HTML vient de l'OCR ou du navigateur : toujours assaini avant d'être affiché dans l'interface.
    return {"id": b.id, "order": b.reading_order, "label": b.label, "bbox": b.bbox, "html": sanitize_html(b.html_current),
            "html_ocr": sanitize_html(b.html_ocr), "confidence": b.confidence, "direction": b.direction,
            "to_check": b.to_check, "error": b.error, "has_crop": bool(b.crop_path),
            "corrections": [{"id": c.id, "source": c.source, "before": sanitize_html(c.before), "after": sanitize_html(c.after),
                             "status": c.status, "model": c.model_name} for c in b.corrections if c.source in ("llm", "ocr2")]}


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
    label: str | None = None  # correction d'une détection : texte pris pour une image, titre pris pour du texte…


# Types qu'un utilisateur peut attribuer à un bloc dans la relecture.
EDITABLE_LABELS = {"Text", "SectionHeader", "ListItem", "Caption", "Footnote", "PageHeader", "PageFooter", "Table",
                   "Picture", "Stamp"}


def _editable(b: Block) -> None:
    if b.page.document.status not in (DocStatus.review.value, DocStatus.done.value):
        raise HTTPException(status.HTTP_409_CONFLICT, "Le document n'est pas modifiable maintenant.")


def _ensure_crop(b: Block) -> None:
    """Un bloc devenu image a besoin de sa découpe dans l'image de la page."""
    if b.crop_path and storage.resolve(b.crop_path).is_file():
        return
    page = b.page
    if not page.image_path:
        return
    import cv2
    import numpy as np

    img = cv2.imdecode(np.fromfile(str(storage.resolve(page.image_path)), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is not None:
        document_model.save_crop(b, page.document, img)


@router.patch("/blocks/{block_id}")
def patch_block(block_id: int, body: BlockPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    b = _owned_block(db, user, block_id)
    _editable(b)
    if body.label is not None and body.label != b.label:
        if body.label not in EDITABLE_LABELS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Type de bloc inconnu.")
        b.label = body.label
        if body.label in ("Picture", "Stamp"):
            _ensure_crop(b)
        b.to_check = False
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


@router.delete("/blocks/{block_id}", status_code=204)
def delete_block(block_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    """Supprime un bloc détecté à tort (bruit, signature lue comme du texte, doublon)."""
    b = _owned_block(db, user, block_id)
    _editable(b)
    if b.crop_path:
        try:
            storage.resolve(b.crop_path).unlink(missing_ok=True)
        except OSError:
            pass
    db.delete(b)
    db.commit()


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
    return FileResponse(_owned_file(db, user, exp.path), filename=docx_name(doc.original_filename),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Cache-Control": "private, no-store"})
