"""Second avis sur la mise en page : détecteur Heron de Docling (RT-DETRv2, 43 M paramètres, Apache-2.0).

Contrairement au modèle de mise en page de Surya (génératif : il peut sauter une zone ou changer d'avis pour quelques
pixels), un détecteur donne des boîtes précises et stables. Il tourne sur processeur : aucune VRAM prise au GPU
partagé. Les poids viennent du cache Hugging Face local (jamais de téléchargement ici).
"""
from __future__ import annotations

import logging

from PIL import Image

log = logging.getLogger("ocr.heron")
REPO = "docling-project/docling-layout-heron"

# Classes Heron → étiquettes de Surya (celles que le reste de l'application comprend).
HERON_TO_SURYA = {
    "Caption": "Caption", "Footnote": "Footnote", "Formula": "Equation", "List-item": "ListItem",
    "Page-footer": "PageFooter", "Page-header": "PageHeader", "Picture": "Picture", "Section-header": "SectionHeader",
    "Table": "Table", "Text": "Text", "Title": "SectionHeader", "Document Index": "TableOfContents", "Code": "Code",
    "Checkbox-Selected": "Text", "Checkbox-Unselected": "Text", "Form": "Form", "Key-Value Region": "Text",
}
CLASSES = ["Caption", "Footnote", "Formula", "List-item", "Page-footer", "Page-header", "Picture", "Section-header",
           "Table", "Text", "Title", "Document Index", "Code", "Checkbox-Selected", "Checkbox-Unselected", "Form",
           "Key-Value Region"]


class HeronDetector:
    def __init__(self, threshold: float = 0.5, device: str = "cpu"):
        import torch
        from huggingface_hub import snapshot_download
        from transformers import RTDetrImageProcessor, RTDetrV2ForObjectDetection

        path = snapshot_download(REPO, local_files_only=True)
        self._torch = torch
        self.processor = RTDetrImageProcessor.from_pretrained(path)
        self.model = RTDetrV2ForObjectDetection.from_pretrained(path).to(device).eval()
        self.device, self.threshold = device, threshold

    def __call__(self, image: Image.Image) -> list[tuple[str, tuple[float, float, float, float], float]]:
        """[(étiquette Surya, boîte en pixels de l'image, score)]."""
        torch = self._torch
        inputs = self.processor(images=[image.convert("RGB")], return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        res = self.processor.post_process_object_detection(
            outputs, target_sizes=torch.tensor([image.size[::-1]]), threshold=self.threshold)[0]
        out = []
        for score, label, box in zip(res["scores"], res["labels"], res["boxes"]):
            name = CLASSES[int(label)] if int(label) < len(CLASSES) else "Text"
            out.append((HERON_TO_SURYA.get(name, "Text"), tuple(float(v) for v in box.tolist()), float(score)))
        return out


def load(threshold: float = 0.5) -> HeronDetector | None:
    try:
        return HeronDetector(threshold)
    except Exception as e:  # noqa: BLE001 - poids absents (hors ligne) : on garde la mise en page de Surya seule
        log.warning("détecteur Heron indisponible (%s) : pas de second avis sur la mise en page", type(e).__name__)
        return None
