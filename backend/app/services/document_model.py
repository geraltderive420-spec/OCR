"""Construction du modèle intermédiaire : document → pages → blocs (PLAN.md §7.7).

- direction de chaque bloc d'après la proportion de lettres arabes ;
- repérage des en-têtes / pieds de page répétés d'une page à l'autre ;
- découpes d'images (blocs visuels et tampons) dans l'image d'ORIGINE ;
- blocs « à vérifier » : confiance basse, erreur, tampon recouvrant du texte.
"""
from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy.orm import Session

from app.models import Block, Document, Page
from app.services import storage
from app.services.ocr import VISUAL_LABELS, OcrPage
from app.services.ocr.html_utils import direction_of, to_text
from app.services.stamps import detect_stamps

LOW_CONFIDENCE = 0.7
HEADER_LABELS = {"PageHeader", "PageFooter"}


def _norm_repeat(text: str) -> str:
    """Normalise pour comparer des en-têtes d'une page à l'autre (numéros de page ignorés)."""
    return re.sub(r"\d+", "#", to_text(text).lower()).strip()


def _crop(image_bgr: np.ndarray, bbox, pad: int = 6) -> np.ndarray | None:
    h, w = image_bgr.shape[:2]
    x0, y0, x1, y1 = bbox
    x0, y0, x1, y1 = max(0, int(x0) - pad), max(0, int(y0) - pad), min(w, int(x1) + pad), min(h, int(y1) + pad)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return image_bgr[y0:y1, x0:x1]


def save_blocks(db: Session, doc: Document, page: Page, ocr: OcrPage, original_bgr: np.ndarray) -> list[Block]:
    """Remplace les blocs de la page par le résultat OCR ; découpe les images ; détecte les tampons."""
    for old in list(page.blocks):
        db.delete(old)
    db.flush()

    blocks: list[Block] = []
    visual_boxes = []
    text_boxes = []
    for i, b in enumerate(ocr.blocks):
        text = to_text(b.html)
        blk = Block(
            page_id=page.id, reading_order=i, label=b.label, bbox=[round(v, 1) for v in b.bbox],
            html_ocr=b.html, html_current=b.html, confidence=b.confidence,
            direction=direction_of(text), error=b.error,
            to_check=b.error or (b.confidence is not None and b.confidence < LOW_CONFIDENCE and b.label not in VISUAL_LABELS),
        )
        if b.label in VISUAL_LABELS:
            visual_boxes.append(tuple(b.bbox))
            _save_crop(blk, doc, original_bgr)
        else:
            text_boxes.append(tuple(b.bbox))
        blocks.append(blk)

    # Tampons et signatures non reconnus comme images par le modèle.
    for st in detect_stamps(original_bgr, visual_boxes, text_boxes):
        blk = Block(page_id=page.id, reading_order=len(blocks), label="Stamp", bbox=[round(v, 1) for v in st.bbox],
                    html_ocr="", html_current="", confidence=None, direction="ltr", to_check=st.overlaps_text)
        _save_crop(blk, doc, original_bgr)
        blocks.append(blk)

    # Les tampons s'insèrent dans l'ordre de lecture d'après leur position verticale.
    blocks.sort(key=lambda x: (0 if x.label != "Stamp" else 1, x.reading_order))
    ordered = [b for b in blocks if b.label != "Stamp"]
    for st in [b for b in blocks if b.label == "Stamp"]:
        idx = 0
        for k, b in enumerate(ordered):
            if b.bbox[1] <= st.bbox[1]:
                idx = k + 1
        ordered.insert(idx, st)
    for n, b in enumerate(ordered):
        b.reading_order = n
        db.add(b)
    db.flush()

    confs = [b.confidence for b in ordered if b.confidence is not None]
    page.mean_confidence = float(np.mean(confs)) if confs else None
    page.status = "ocr_done"
    return ordered


def _save_crop(blk: Block, doc: Document, original_bgr: np.ndarray) -> None:
    crop = _crop(original_bgr, blk.bbox)
    if crop is None:
        return
    path, rel = storage.new_path("crops", doc.user_id, doc.id, ".png")
    ok, buf = cv2.imencode(".png", crop)
    if ok:
        Path(path).write_bytes(buf.tobytes())
        blk.crop_path = rel


def repeated_headers(doc: Document) -> dict[str, str]:
    """{'PageHeader': html, 'PageFooter': html} si le même en-tête / pied revient sur ≥ 2 pages (ou 1 seule page)."""
    out: dict[str, str] = {}
    if not doc.pages:
        return out
    for label in HEADER_LABELS:
        counts: dict[str, tuple[int, str]] = {}
        for p in doc.pages:
            for b in p.blocks:
                if b.label == label and to_text(b.html_current):
                    key = _norm_repeat(b.html_current)
                    n, html = counts.get(key, (0, b.html_current))
                    counts[key] = (n + 1, html)
        if counts:
            key, (n, html) = max(counts.items(), key=lambda kv: kv[1][0])
            if n >= 2 or len(doc.pages) == 1:
                out[label] = html
    return out
