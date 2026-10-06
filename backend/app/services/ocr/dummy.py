"""Moteur factice : sert aux tests et au développement sans GPU. Produit une page fixe et déterministe."""
from __future__ import annotations

from PIL import Image

from app.services.ocr import OcrBlock, OcrPage


class DummyEngine:
    name = "dummy"

    def start(self) -> None:
        pass

    def recognize(self, image: Image.Image) -> OcrPage:
        w, h = image.size
        return OcrPage([
            OcrBlock("PageHeader", "Ministère de test", (w * .1, h * .02, w * .9, h * .06), 0.97),
            OcrBlock("SectionHeader", "Titre du documcnt", (w * .1, h * .08, w * .9, h * .13), 0.96),
            OcrBlock("Text", "Ceci est un paragraphe de démonstration, référence 2024/118 du 12/03/2024.",
                     (w * .1, h * .15, w * .9, h * .30), 0.93),
            OcrBlock("Text", "هذا نص تجريبي باللغة العربية رقم 45", (w * .1, h * .32, w * .9, h * .40), 0.62),
            OcrBlock("Table", "<table><tr><th>Nom</th><th>Qté</th></tr><tr><td>Alpha</td><td>3</td></tr>"
                              "<tr><td colspan='2'>Total : 3</td></tr></table>", (w * .1, h * .42, w * .9, h * .60), 0.9),
            OcrBlock("Picture", "", (w * .1, h * .62, w * .5, h * .80), None),
            OcrBlock("PageFooter", "Page 1", (w * .4, h * .94, w * .6, h * .97), 0.9),
        ])

    def stop(self) -> None:
        pass
