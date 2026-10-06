"""Surya 2 via un `llama-server` possédé par le worker. API vérifiée contre surya-ocr 0.22.1.

Le serveur est lancé par `LlamaServer` ; Surya s'y attache par SURYA_INFERENCE_URL (aucun démarrage automatique,
aucun fichier-sentinelle).
"""
from __future__ import annotations

import logging
import os

from PIL import Image

from app.services.gpu import LlamaServer
from app.services.ocr import OcrBlock, OcrPage
from app.services.ocr.html_utils import has_table_markup

log = logging.getLogger("ocr.surya2")


class Surya2Engine:
    name = "surya2"

    def __init__(self, server: LlamaServer | None = None):
        self.server = server or LlamaServer()
        self._layout = self._rec = self._table = self._manager = None

    def start(self) -> None:
        self.server.start()
        # À fixer avant tout import de surya : ses réglages lisent l'environnement une seule fois.
        os.environ["SURYA_INFERENCE_BACKEND"] = "llamacpp"
        os.environ["SURYA_INFERENCE_URL"] = self.server.base_url
        os.environ["SURYA_INFERENCE_PARALLEL"] = "1"
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

    def recognize(self, image: Image.Image) -> OcrPage:
        if self._rec is None:
            raise RuntimeError("Moteur non démarré")
        layouts = self._layout([image])
        pred = self._rec([image], layouts)[0]
        blocks: list[OcrBlock] = []
        for b in pred.blocks:
            bbox = tuple(float(v) for v in b.bbox)
            html = b.html or ""
            err = bool(getattr(b, "error", False))
            if b.label == "Table" and html and not has_table_markup(html):
                html = self._table_html(image, bbox) or html
            blocks.append(OcrBlock(b.label, html, bbox, float(b.confidence) if b.confidence is not None else None, err))
        return OcrPage(blocks)

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
        self._layout = self._rec = self._table = self._manager = None
        self.server.stop()
