"""Surya 2 via un `llama-server` possédé par le worker. API vérifiée contre surya-ocr 0.22.1.

Le serveur est lancé par `LlamaServer` ; Surya s'y attache par SURYA_INFERENCE_URL (aucun démarrage automatique,
aucun fichier-sentinelle).
"""
from __future__ import annotations

import logging
import os

import cv2
import numpy as np

from PIL import Image

from app.core.config import get_settings
from app.services.gpu import LlamaServer
from app.services.ocr import VISUAL_LABELS, OcrBlock, OcrPage
from app.services.ocr.html_utils import escape, has_table_markup, to_text
from app.services.ocr.layout_refine import RefinedBlock, area, has_printed_text, inter, refine, token_estimate
from app.services.stamps import erase_stamps

log = logging.getLogger("ocr.surya2")
TEXT_LABELS = {"Text", "SectionHeader", "PageHeader", "PageFooter", "Caption", "Footnote", "ListItem"}


def token_budget(count: int) -> int:
    """Budget de génération d'un bloc. Surya accorde l'estimation du modèle de mise en page + 100 tokens, sans
    signaler une sortie coupée : l'arabe (bien plus de tokens par mot) perdait la fin des blocs. Mesure : +5 points
    de mots retrouvés sur une page arabe. Le plafond de Surya (SURYA_MAX_TOKENS_BLOCK_CEILING) s'applique ensuite.
    """
    return max(2 * count, count + 300)


class Surya2Engine:
    name = "surya2"

    def __init__(self, server: LlamaServer | None = None):
        self.server = server or LlamaServer()
        self._layout = self._rec = self._table = self._manager = self._det = None

    def start(self) -> None:
        self.server.start()
        try:
            self._load()
        except Exception:
            self.server.stop()  # sinon llama-server reste orphelin et occupe le port : « attente du GPU » sans fin
            self._layout = self._rec = self._table = self._manager = self._det = None
            raise

    def _load(self) -> None:
        # À fixer avant tout import de surya : ses réglages lisent l'environnement une seule fois.
        os.environ["SURYA_INFERENCE_BACKEND"] = "llamacpp"
        os.environ["SURYA_INFERENCE_URL"] = self.server.base_url
        os.environ["SURYA_INFERENCE_PARALLEL"] = str(max(1, get_settings().llama_parallel))
        os.environ["SURYA_INFERENCE_AUTOSTART"] = "false"
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from surya.inference import SuryaInferenceManager
        from surya.layout import LayoutPredictor
        from surya.recognition import RecognitionPredictor
        from surya.table_rec import TableRecPredictor

        self._manager = SuryaInferenceManager()
        self._layout = LayoutPredictor(self._manager)
        self._rec = RecognitionPredictor(self._manager)
        self._table = TableRecPredictor(self._manager)
        self._manager.start()
        self._det = self._load_line_detector()

    @staticmethod
    def _load_line_detector():
        """Détecteur de lignes de Surya (petit modèle torch), pour recaler la mise en page. Facultatif."""
        s = get_settings()
        if not s.layout_refine:
            return None
        try:
            import torch
            from surya.detection import DetectionPredictor

            # Surya choisit la demi-précision dès qu'un GPU existe : sur processeur, c'est ~80 fois plus lent.
            dtype = torch.float32 if s.line_detector_device == "cpu" else torch.float16
            return DetectionPredictor.local(device=s.line_detector_device, dtype=dtype)
        except Exception as e:  # noqa: BLE001 - poids absents (hors ligne) : on garde la mise en page brute
            log.warning("détecteur de lignes indisponible (%s) : mise en page non recalée", type(e).__name__)
            return None

    def _detect_lines(self, image: Image.Image) -> list | None:
        if self._det is None:
            return None
        try:
            return [tuple(b.bbox) for b in self._det([image])[0].bboxes]
        except Exception as e:  # noqa: BLE001
            log.warning("détection de lignes en échec (%s) : mise en page non recalée", type(e).__name__)
            return None

    def _refined_layout(self, image: Image.Image, layout, stamp_boxes: list, lines: list | None):
        """Boîtes recalées sur les lignes détectées + blocs pour le texte qu'aucune boîte ne couvrait.

        `image` est la page sans l'encre des tampons ; une « image » de la mise en page qui n'est qu'un tampon posé
        sur du texte imprimé devient aussi un bloc de texte (sinon ce texte n'est jamais lu).
        """
        from surya.layout.schema import LayoutBox

        blocks = [(b.label, tuple(b.bbox)) for b in layout.bboxes]
        see_through = {i for i, (label, box) in enumerate(blocks)
                       if label in VISUAL_LABELS and any(inter(box, st) >= 0.6 * area(box) for st in stamp_boxes)}
        if lines is None:
            refined = [RefinedBlock(label, box, i) for i, (label, box) in enumerate(blocks)]
        else:
            refined = refine(blocks, lines, image.width, image.height, see_through=see_through)
        out = refined
        if lines is None and see_through:  # sans lignes détectées : on lit toute la zone du tampon comme du texte
            gray = np.asarray(image.convert("L"))
            out = []
            for r in refined:
                out.append(r)
                if r.source in see_through and has_printed_text(gray, r.bbox):
                    out.append(RefinedBlock("Text", r.bbox, None, [r.bbox]))
        boxes = []
        for i, r in enumerate(out):
            x0, y0, x1, y1 = r.bbox
            src = layout.bboxes[r.source] if r.source is not None else None
            boxes.append(LayoutBox(
                polygon=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], label=r.label,
                raw_label=src.raw_label if src else "Text", position=i,
                count=src.count if src else token_estimate(r.lines),
                confidence=src.confidence if src else None))
        return layout.model_copy(update={"bboxes": boxes})

    def recognize(self, image: Image.Image) -> OcrPage:
        if self._rec is None:
            raise RuntimeError("Moteur non démarré")
        rgb = np.asarray(image.convert("RGB"))
        clean_bgr, stamps = (erase_stamps(np.ascontiguousarray(rgb[..., ::-1])) if get_settings().stamp_erase
                             else (None, []))
        clean = Image.fromarray(np.ascontiguousarray(clean_bgr[..., ::-1])) if stamps else image
        # Mise en page et lecture sur la page SANS tampons : un tampon n'est plus pris pour une image qui masque
        # le texte. Les tampons sont repérés à part (couleur) et ajoutés au document avec leur découpe d'origine.
        layout = self._layout([clean])[0]
        lines = self._detect_lines(clean)
        if not layout.error and (lines is not None or stamps):
            layout = self._refined_layout(clean, layout, [s.bbox for s in stamps], lines)
        for box in layout.bboxes:
            box.count = token_budget(box.count)
        pred = self._rec([clean], [layout])[0]
        items = sorted(pred.blocks, key=lambda x: x.reading_order)
        if stamps:
            self._keep_fuller_reading(image, layout, items, [s.bbox for s in stamps])
        rescued = self._reread_short(clean, items, lines or [])
        blocks: list[OcrBlock] = []
        for b in items:
            bbox = tuple(float(v) for v in b.bbox)
            html = rescued.get(id(b), b.html or "")
            err = bool(getattr(b, "error", False)) and id(b) not in rescued
            if b.label == "Table" and html and not has_table_markup(html):
                html = self._table_html(clean, bbox) or html
            if b.label in TEXT_LABELS and not err and not to_text(html):
                continue  # bloc de texte vide (zone sans texte lisible) : rien à garder
            label = "Picture" if self._is_logo(np.asarray(clean), b.label, bbox, html) else b.label
            blocks.append(OcrBlock(label, html, bbox, float(b.confidence) if b.confidence is not None else None, err))
        return OcrPage(blocks)

    @staticmethod
    def _is_logo(rgb: np.ndarray, label: str, bbox, html: str) -> bool:
        """Logo d'en-tête pris pour du texte (« PSE Electronic Solutions ») : court, en haut de page, encre colorée.

        Le texte d'une lettre est noir ; les tampons bleus / rouges ont déjà été effacés de cette image.
        """
        if label not in TEXT_LABELS or len(to_text(html)) > 40 or bbox[3] > 0.25 * rgb.shape[0]:
            return False
        x0, y0, x1, y1 = (int(v) for v in bbox)
        crop = rgb[max(0, y0):y1, max(0, x0):x1]
        if crop.size == 0:
            return False
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        sat = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)[..., 1] > 60
        n = int(ink.sum())
        return n > 0 and float((sat & (ink > 0)).sum()) / n >= 0.15

    def _keep_fuller_reading(self, original: Image.Image, layout, items: list, stamp_boxes: list) -> None:
        """Blocs touchés par un tampon : relus aussi sur la page d'origine ; on garde la lecture sans tampon, sauf si
        celle d'origine est nettement plus complète (le modèle réagit parfois fortement à l'image retouchée)."""
        from surya.layout.schema import LayoutResult

        touched = [b for b in items if b.label in TEXT_LABELS | {"Table"}
                   and any(inter(tuple(b.bbox), st) > 0 for st in stamp_boxes)]
        if not touched:
            return
        boxes = [layout.bboxes[b.reading_order] for b in touched]
        again = self._rec([original], [LayoutResult(bboxes=boxes, image_bbox=layout.image_bbox)])[0]
        for b, alt in zip(touched, sorted(again.blocks, key=lambda x: x.reading_order)):
            if not alt.error and len(to_text(alt.html or "")) > 1.25 * len(to_text(b.html or "")):
                b.html, b.confidence, b.error = alt.html, alt.confidence, False

    def _reread_short(self, image: Image.Image, items: list, lines: list) -> dict[int, str]:
        """Relit ligne par ligne les blocs de texte revenus vides, en erreur ou nettement trop courts pour leurs lignes.

        Le modèle renvoie parfois une chaîne vide pour un gros bloc lisible (bandeau grisé, police creuse), ou s'arrête
        après une ligne abîmée (ligne coupée en haut de page) en perdant les suivantes. {id(bloc): html}.
        """
        from surya.layout.schema import LayoutBox, LayoutResult

        gray = None
        todo: list[tuple[int, list]] = []
        current: dict[int, str] = {}
        for b in items:
            if b.label not in TEXT_LABELS:
                continue
            text = to_text(b.html or "")
            box = tuple(float(v) for v in b.bbox)
            inside = sorted((ln for ln in lines if inter(ln, box) >= 0.5 * area(ln)), key=lambda ln: (ln[1], ln[0]))
            # ~0,45 corps par caractère : en dessous de 45 % de ce qu'annoncent les lignes, il manque du texte.
            expected = sum((ln[2] - ln[0]) / max(1.0, 0.45 * (ln[3] - ln[1])) for ln in inside)
            if text and (len(inside) < 2 or len(text) >= 0.45 * expected):
                continue
            if not inside:
                if text:
                    continue
                if gray is None:
                    gray = np.asarray(image.convert("L"))
                if not has_printed_text(gray, box):
                    continue
                inside = [box]
            todo.append((id(b), inside))
            current[id(b)] = text
        if not todo:
            return {}
        boxes, owners = [], []
        for owner, inside in todo:
            for ln in inside:
                x0, y0, x1, y1 = ln
                boxes.append(LayoutBox(polygon=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], label="Text", raw_label="Text",
                                       position=len(boxes), count=token_budget(token_estimate([ln]))))
                owners.append(owner)
        res = self._rec([image], [LayoutResult(bboxes=boxes, image_bbox=[0, 0, image.width, image.height])])[0]
        parts: dict[int, list[str]] = {}
        for owner, blk in zip(owners, sorted(res.blocks, key=lambda x: x.reading_order)):
            text = to_text(blk.html or "")
            if text:
                parts.setdefault(owner, []).append(escape(text))
        out = {}
        for owner, p in parts.items():
            if len(" ".join(p)) > 1.3 * len(current.get(owner, "")):  # seulement si la relecture apporte du texte
                out[owner] = "<p>" + "<br/>".join(p) + "</p>"
        log.info("%d bloc(s) relu(s) ligne par ligne", len(out))
        return out

    def _table_html(self, image: Image.Image, bbox: tuple[float, float, float, float]) -> str | None:
        """Repli : reconnaissance dédiée du tableau (cellules fusionnées) sur la découpe."""
        try:
            x0, y0, x1, y1 = (int(v) for v in bbox)
            res = self._table.predict_full([image.crop((max(0, x0), max(0, y0), x1, y1))])[0]
            return res.html if res and not res.error else None
        except Exception:  # noqa: BLE001 - jamais de contenu dans les journaux
            log.warning("Reconnaissance de tableau en échec (%s)", "repli sur le texte du bloc")
            return None

    def stop(self) -> None:
        self._layout = self._rec = self._table = self._manager = self._det = None
        self.server.stop()
