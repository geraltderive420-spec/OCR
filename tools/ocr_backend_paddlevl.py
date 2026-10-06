"""Adaptateur PaddleOCR-VL-1.6 (0,9 B) pour tools/evaluate.py.

À lancer depuis un environnement dédié (D:\\ocr_work\\venv\\paddlevl) : paddleocr[doc-parser] 3.7.x,
moteur « transformers » (PyTorch). Le moteur Paddle natif exige une architecture GPU >= 7.0 ; la
GTX 1050 Ti (Pascal, sm_61) n'est pas prise en charge, alors que PyTorch cu118 l'est.

Variables facultatives :
  PADDLEVL_DEVICE  gpu:0 (défaut) | cpu
  PADDLEVL_DTYPE   float16 (défaut, tient dans 4 Go) | float32 | bfloat16
Les caches (modèles) se redirigent avec PADDLE_PDX_CACHE_HOME / HF_HOME (voir docs/phase0-paddleocr-vl.md).
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image

from ocr_backends import Block, PageResult, html_to_text

# Étiquettes PaddleOCR-VL (PP-DocLayout) -> étiquettes de Surya.
LABEL_MAP = {
    "text": "Text",
    "paragraph_title": "SectionHeader",
    "doc_title": "SectionHeader",
    "title": "SectionHeader",
    "abstract": "Text",
    "content": "TableOfContents",
    "reference": "Text",
    "reference_content": "Text",
    "vertical_text": "Text",
    "aside_text": "Text",
    "algorithm": "Code",
    "table": "Table",
    "image": "Picture",
    "chart": "Figure",
    "figure_title": "Caption",
    "header": "PageHeader",
    "header_image": "PageHeader",
    "footer": "PageFooter",
    "footer_image": "PageFooter",
    "number": "PageFooter",
    "footnote": "Footnote",
    "vision_footnote": "Footnote",
    "formula": "Equation",
    "display_formula": "Equation",
    "inline_formula": "Equation",
    "formula_number": "Equation",
    "seal": "Picture",
    "stamp": "Picture",
    "list": "ListGroup",
    "list_item": "ListItem",
}

VISUAL = {"Picture", "Figure"}


class Backend:
    name = "paddlevl"

    def load(self) -> None:
        from paddleocr import PaddleOCRVL

        device = os.environ.get("PADDLEVL_DEVICE", "gpu:0")
        dtype = os.environ.get("PADDLEVL_DTYPE", "float16")
        self._pipe = PaddleOCRVL(
            engine="transformers",
            device=device,
            engine_config={"dtype": dtype},
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_chart_recognition=False,
            use_seal_recognition=False,
        )

    @staticmethod
    def _to_block(b) -> Block:
        raw = str(getattr(b, "label", "text"))
        label = LABEL_MAP.get(raw, "Text")
        content = getattr(b, "content", "") or ""
        if label == "Table" or "<table" in content[:50].lower():
            text = html_to_text(content)
        elif label in VISUAL:
            text = html_to_text(content) if content else ""
        else:
            text = html_to_text(content) if "<" in content else content.strip()
        bbox = getattr(b, "bbox", None)
        return Block(label, text, None, tuple(float(x) for x in bbox) if bbox is not None else None)

    def run(self, image: Image.Image) -> PageResult:
        bgr = np.array(image.convert("RGB"))[:, :, ::-1].copy()
        results = list(self._pipe.predict(bgr))
        blocks: list[Block] = []
        for res in results:
            for b in res["parsing_res_list"]:
                blocks.append(self._to_block(b))
        return PageResult(blocks)

    def close(self) -> None:
        if hasattr(self, "_pipe"):
            del self._pipe
        try:
            import gc

            import torch

            gc.collect()
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
