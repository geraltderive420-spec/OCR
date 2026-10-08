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
from app.services.ocr_config import OcrConfig
from app.services import app_settings, document_model, preprocess, storage
from app.services.docx_builder import BlockModel, DocxBuilder, Options, PageModel
from app.services.llm import LlmError, OllamaClient, correct_block
from app.services.ocr import VISUAL_LABELS, OcrEngine, OcrUnavailable
from app.services.ocr.html_utils import to_text

log = logging.getLogger("ocr.pipeline")
RENDER_DPI = 200
MIN_DPI, MAX_DPI = 100, 400


class Cancelled(RuntimeError):
    """L'utilisateur a annulé le document pendant son traitement."""


def check_cancel(db: Session, doc: Document) -> None:
    """Lu entre deux pages : une annulation demandée pendant le traitement l'arrête au plus vite."""
    db.refresh(doc, ["cancel_requested"])
    if doc.cancel_requested:
        raise Cancelled()


def render_dpi(page_width_pt: float, target_px: int) -> int:
    """Résolution de rendu donnant une page de `target_px` pixels de large (0 : 200 dpi), bornée à 100-400 dpi.

    Surya 2 réduit lui-même au-delà de ~6 mégapixels pour la mise en page, mais lit chaque bloc sur l'image fournie :
    une page plus large aide les petits caractères (et les points de l'arabe), au prix du temps de calcul.
    """
    if target_px <= 0 or page_width_pt <= 0:
        return RENDER_DPI
    return int(max(MIN_DPI, min(MAX_DPI, round(target_px * 72 / page_width_pt))))
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


def render_and_preprocess(db: Session, doc: Document, cfg: OcrConfig | None = None) -> None:
    cfg = cfg or OcrConfig.from_env()
    opts = doc_options(doc)
    profile = opts["preprocess"] if opts["preprocess"] != "auto" else app_settings.get_setting(db, "default_preprocess")
    src = storage.doc_dir("uploads", doc.user_id, doc.id) / doc.stored_name
    pdf = pdfium.PdfDocument(str(src))
    try:
        n = len(pdf)
        if not doc.pages:
            for i in range(n):
                dpi = render_dpi(pdf[i].get_width(), cfg.page_width)
                db.add(Page(document_id=doc.id, index=i, dpi=dpi, status="pending"))
            doc.page_count = n
            db.commit()
            db.refresh(doc)
        _set(doc, db, DocStatus.preprocessing, 0.0)
        for page in doc.pages:
            if page.status != "pending":
                continue
            check_cancel(db, doc)
            try:
                page.dpi = render_dpi(pdf[page.index].get_width(), cfg.page_width)  # réglage du moment (reprise)
                pil = pdf[page.index].render(scale=page.dpi / 72).to_pil().convert("RGB")
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
        check_cancel(db, doc)
        try:
            work = storage.resolve(page.preprocessed_path) if page.preprocessed_path else None
            if work is None or not work.is_file():  # image de travail perdue : l'image d'origine redressée suffit
                work = storage.resolve(page.image_path)
            pre = Image.open(work).convert("RGB")
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


# ------------------------------------------------------------------ 2 bis. seconde lecture des blocs douteux

SECOND_LABELS = {"Text", "SectionHeader", "PageHeader", "PageFooter", "Caption", "Footnote", "ListItem", "Form"}


def second_reading_candidates(doc: Document, cfg: OcrConfig | None = None) -> list[Block]:
    threshold = (cfg or OcrConfig.from_env()).second_reader_threshold
    return [b for p in doc.pages if p.status == "ocr_done" for b in p.blocks
            if b.label in SECOND_LABELS and (b.confidence is None or b.confidence < threshold)]


def run_second_reading(db: Session, doc: Document, cfg: OcrConfig | None = None) -> dict:
    """Relit les blocs douteux avec PaddleOCR-VL et confronte les deux lectures (consensus.decide).

    Lecture remplacée : correction « ocr2 » appliquée et annulable dans la relecture. Désaccord : bloc « à vérifier ».
    Le moteur principal doit déjà être arrêté si la VRAM ne suffit pas pour deux serveurs (voir le worker).
    """
    from app.services.gpu import LlamaServerError
    from app.services.ocr import consensus, second_reader
    from app.services.ocr.html_utils import escape
    from app.services.stamps import erase_stamps

    cfg = cfg or OcrConfig.from_env()
    stats = {"relus": 0, "remplaces": 0, "desaccords": 0, "ignoree": False}
    blocks = second_reading_candidates(doc, cfg)
    if not blocks or not cfg.second_reader or not second_reader.available():
        stats["ignoree"] = bool(blocks)
        return stats
    reader = second_reader.SecondReader(config=cfg)
    try:
        reader.start()
    except LlamaServerError:
        log.warning("document %s : second lecteur indisponible, seconde lecture ignorée", doc.id)
        stats["ignoree"] = True
        return stats
    try:
        images: dict[int, np.ndarray] = {}
        for i, b in enumerate(blocks):
            if i % 10 == 0:
                check_cancel(db, doc)
            page = b.page
            if page.id not in images:
                bgr = cv2.imdecode(np.fromfile(str(storage.resolve(page.image_path)), dtype=np.uint8), cv2.IMREAD_COLOR)
                images[page.id] = cv2.cvtColor(erase_stamps(bgr)[0], cv2.COLOR_BGR2RGB)
            img = images[page.id]
            h, w = img.shape[:2]
            x0, y0, x1, y1 = (int(v) for v in b.bbox)
            crop = Image.fromarray(img[max(0, y0 - 4):min(h, y1 + 4), max(0, x0 - 4):min(w, x1 + 4)])
            try:
                text, conf = reader.read(crop)
            except Exception as e:  # noqa: BLE001 - jamais de contenu dans les journaux
                log.warning("document %s : seconde lecture d'un bloc en échec (%s)", doc.id, type(e).__name__)
                continue
            stats["relus"] += 1
            verdict = consensus.decide(to_text(b.html_current), b.confidence, text, conf)
            if not verdict.agree:
                stats["desaccords"] += 1
                b.to_check = True
            if verdict.use_second:
                new = "<p>" + "<br/>".join(escape(line) for line in text.splitlines() if line.strip()) + "</p>"
                db.add(Correction(block_id=b.id, source="ocr2", before=b.html_current, after=new,
                                  model_name=second_reader.ALIAS, status=CorrectionStatus.auto_applied.value))
                b.html_current = new
                stats["remplaces"] += 1
            db.commit()
            _set(doc, db, DocStatus.ocr, 0.8 + 0.05 * (i + 1) / len(blocks))
    finally:
        reader.stop()
    return stats


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
            if i % 10 == 0:
                check_cancel(db, doc)
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
