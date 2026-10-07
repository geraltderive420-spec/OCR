"""Traitement d'un document (PLAN.md §7) : rendu, prétraitement, OCR, correction, relecture, export.

Chaque étape est reprenable : l'état est dans la base (statut de page), pas en mémoire. Une page en échec ne fait
pas échouer le document. Aucun contenu (texte, noms de fichier) dans les journaux.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (Block, Correction, CorrectionStatus, DocStatus, Document, Export, Page, utcnow)
from app.services import app_settings, document_model, preprocess, storage
from app.services.docx_builder import BlockModel, DocxBuilder, Options, PageModel
from app.services.llm import LlmError, OllamaClient, correct_block
from app.services.ocr import VISUAL_LABELS, OcrEngine, OcrUnavailable
from app.services.ocr.html_utils import to_text

log = logging.getLogger("ocr.pipeline")
RENDER_DPI = 200
TEXT_SKIP = VISUAL_LABELS | {"Stamp"}

OPTION_DEFAULTS = {"preprocess": "auto", "llm": "off", "review": False, "ollama_model": None, "llm_all": False,
                   "font": None}


def doc_options(doc: Document) -> dict:
    return {**OPTION_DEFAULTS, **(doc.options or {})}


def _set(doc: Document, db: Session, status: DocStatus, progress: float | None = None) -> None:
    doc.status = status.value
    if progress is not None:
        doc.progress = round(progress, 3)
    db.commit()


# ------------------------------------------------------------------ 1. rendu + prétraitement

def count_pdf_pages(path: Path) -> int:
    pdf = pdfium.PdfDocument(str(path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_and_preprocess(db: Session, doc: Document) -> None:
    opts = doc_options(doc)
    profile = opts["preprocess"] if opts["preprocess"] != "auto" else app_settings.get_setting(db, "default_preprocess")
    src = storage.doc_dir("uploads", doc.user_id, doc.id) / doc.stored_name
    pdf = pdfium.PdfDocument(str(src))
    try:
        n = len(pdf)
        if not doc.pages:
            for i in range(n):
                db.add(Page(document_id=doc.id, index=i, dpi=RENDER_DPI, status="pending"))
            doc.page_count = n
            db.commit()
            db.refresh(doc)
        _set(doc, db, DocStatus.preprocessing, 0.0)
        for page in doc.pages:
            if page.status != "pending":
                continue
            try:
                pil = pdf[page.index].render(scale=RENDER_DPI / 72).to_pil().convert("RGB")
                bgr = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
                color, pre_rgb, used, _angle = preprocess.process_page(bgr, profile)
                p1, r1 = storage.new_path("pages", doc.user_id, doc.id, ".jpg")
                p2, r2 = storage.new_path("pages", doc.user_id, doc.id, ".png")
                cv2.imwrite(str(p1), color, [cv2.IMWRITE_JPEG_QUALITY, 90])
                # Image pour l'OCR sans perte (le JPEG abîme les traits fins de l'arabe) ; effacée en fin de job.
                cv2.imwrite(str(p2), cv2.cvtColor(pre_rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 1])
                page.width, page.height = color.shape[1], color.shape[0]
                page.image_path, page.preprocessed_path, page.status = r1, r2, "rendered"
            except Exception as e:  # noqa: BLE001
                page.status, page.error = "error", type(e).__name__
                log.warning("page %s du document %s : échec du rendu (%s)", page.index, doc.id, type(e).__name__)
            db.commit()
            _set(doc, db, DocStatus.preprocessing, 0.3 * (page.index + 1) / max(1, n))
    finally:
        pdf.close()


# ------------------------------------------------------------------ 2. OCR

def run_ocr(db: Session, doc: Document, engine: OcrEngine) -> None:
    _set(doc, db, DocStatus.ocr)
    todo = [p for p in doc.pages if p.status == "rendered"]
    total = max(1, len(doc.pages))
    for page in todo:
        try:
            pre = Image.open(storage.resolve(page.preprocessed_path)).convert("RGB")
            ocr = engine.recognize(pre)
            original = cv2.imdecode(np.fromfile(str(storage.resolve(page.image_path)), dtype=np.uint8), cv2.IMREAD_COLOR)
            document_model.save_blocks(db, doc, page, ocr, original)
        except OcrUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            db.rollback()
            page = db.get(Page, page.id)
            page.status, page.error = "error", type(e).__name__
            log.warning("page %s du document %s : échec OCR (%s)", page.index, doc.id, type(e).__name__)
        db.commit()
        done = sum(1 for p in doc.pages if p.status in ("ocr_done", "error"))
        _set(doc, db, DocStatus.ocr, 0.3 + 0.5 * done / total)


# ------------------------------------------------------------------ 3. correction LLM

def run_correction(db: Session, doc: Document) -> dict:
    opts = doc_options(doc)
    mode = opts["llm"]
    stats = {"proposees": 0, "appliquees": 0, "rejetees": 0, "ignoree": False}
    if mode not in ("auto", "validate"):
        return stats
    s = get_settings()
    model = opts["ollama_model"] or app_settings.get_setting(db, "ollama_model")
    try:
        client = OllamaClient(
            s.ollama_url, model, timeout=app_settings.get_setting(db, "llm_timeout_seconds"),
            think=app_settings.get_setting(db, "llm_think"), keep_alive=app_settings.get_setting(db, "llm_keep_alive_seconds"))
    except LlmError:
        stats["ignoree"] = True
        return stats
    if not client.available():  # Ollama arrêté ou modèle absent : l'application reste utilisable, la correction est sautée
        log.warning("document %s : Ollama indisponible, correction ignorée", doc.id)
        stats["ignoree"] = True
        return stats

    _set(doc, db, DocStatus.correcting)
    threshold = 1.01 if opts["llm_all"] else app_settings.get_setting(db, "llm_confidence_threshold")
    max_change = app_settings.get_setting(db, "llm_max_change_ratio")
    blocks = [b for p in doc.pages for b in p.blocks
              if b.label not in TEXT_SKIP and to_text(b.html_current) and (b.confidence is None or b.confidence < threshold)]
    try:
        for i, b in enumerate(blocks):
            try:
                new, verdict = correct_block(client, b.html_current, max_change)
            except LlmError:
                stats["rejetees"] += 1
                continue
            if not verdict.ok or new == b.html_current:
                stats["rejetees"] += 1
                continue
            auto = mode == "auto"
            db.add(Correction(block_id=b.id, source="llm", before=b.html_current, after=new, model_name=model,
                              status=(CorrectionStatus.auto_applied if auto else CorrectionStatus.proposed).value))
            if auto:
                b.html_current = new
                stats["appliquees"] += 1
            else:
                stats["proposees"] += 1
            db.commit()
            doc.progress = round(0.8 + 0.15 * (i + 1) / max(1, len(blocks)), 3)
            db.commit()
    finally:
        client.unload()  # libère la VRAM : le GPU est partagé avec d'autres applications
    return stats


# ------------------------------------------------------------------ 4. export

def to_models(doc: Document) -> list[PageModel]:
    out = []
    for p in sorted(doc.pages, key=lambda x: x.index):
        if p.status != "ocr_done":
            continue
        blocks = [BlockModel(b.label, b.html_current, tuple(b.bbox), b.direction,
                             str(storage.resolve(b.crop_path)) if b.crop_path else None, b.style)
                  for b in sorted(p.blocks, key=lambda x: x.reading_order)]
        out.append(PageModel(p.width, p.height, blocks, p.dpi))
    return out


def export_docx(db: Session, doc: Document) -> Export:
    _set(doc, db, DocStatus.exporting, 0.97)
    models = to_models(doc)
    repeated = document_model.repeated_headers(doc)
    path, rel = storage.new_path("exports", doc.user_id, doc.id, ".docx")
    opts = Options(arabic_font=app_settings.get_setting(db, "docx_arabic_font"),
                   latin_font=doc_options(doc)["font"] or app_settings.get_setting(db, "docx_latin_font"),
                   columns=app_settings.get_setting(db, "docx_columns"))
    DocxBuilder(models, opts, repeated).save(path)
    for old in list(doc.exports):  # un seul export courant : le précédent est effacé réellement
        try:
            storage.resolve(old.path).unlink(missing_ok=True)
        except OSError:
            pass
        db.delete(old)
    exp = Export(document_id=doc.id, path=rel)
    db.add(exp)
    doc.finished_at = utcnow()
    _set(doc, db, DocStatus.done, 1.0)
    return exp


def cleanup_temporary(doc: Document) -> None:
    """Fin de job : les images prétraitées ne servent plus (la relecture affiche l'image d'origine redressée)."""
    for p in doc.pages:
        if p.preprocessed_path:
            try:
                storage.resolve(p.preprocessed_path).unlink(missing_ok=True)
            except OSError:
                pass
            p.preprocessed_path = None
