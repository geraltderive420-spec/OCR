"""Adaptateurs OCR interchangeables pour tools/evaluate.py.

Chaque adaptateur transforme une image de page en une liste de blocs normalisés
(étiquette, texte, confiance, boîte). Le code d'évaluation ne dépend que de cette forme,
ce qui permet de comparer Surya 1, Surya 2, PaddleOCR-VL, dots.ocr... avec les mêmes mesures.

Les adaptateurs importent leurs bibliothèques à la demande : chaque modèle peut vivre dans
son propre environnement virtuel (Surya 1 et Surya 2 ne coexistent pas dans le même).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image


@dataclass
class Block:
    label: str  # Table, Picture, Figure, Text, SectionHeader...
    text: str
    confidence: float | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class PageResult:
    blocks: list[Block] = field(default_factory=list)


class Backend(Protocol):
    name: str

    def load(self) -> None: ...
    def run(self, image: Image.Image) -> PageResult: ...
    def close(self) -> None: ...


_TAG = re.compile(r"<[^>]+>")


def html_to_text(s: str) -> str:
    """Texte brut d'un fragment HTML (cellules de tableau séparées par des espaces)."""
    s = re.sub(r"</(td|th|tr|p|div|li|h\d)>|<br\s*/?>", " ", s)
    return html.unescape(_TAG.sub("", s)).strip()


class DummyBackend:
    """Backend de test du harnais : ne charge aucun modèle, renvoie un résultat fixe."""

    name = "dummy"

    def load(self) -> None:
        pass

    def run(self, image: Image.Image) -> PageResult:
        w, h = image.size
        return PageResult([
            Block("SectionHeader", "Titre de test", 0.95, (0, 0, w, h * 0.1)),
            Block("Text", "Texte de démonstration 123", 0.8, (0, h * 0.1, w, h * 0.5)),
            Block("Table", "A B C D", 0.7, (0, h * 0.5, w, h * 0.8)),
        ])

    def close(self) -> None:
        pass


class Surya1Backend:
    """Surya 1.x (testé contre surya-ocr 0.16.7 : FoundationPredictor + RecognitionPredictor + LayoutPredictor).

    Les lignes OCR sont rattachées au bloc de mise en page qui contient leur centre ;
    les lignes hors de tout bloc vont dans un bloc « Unassigned ».
    """

    name = "surya1"

    # Réglages repris de GED_REACT (backend/app/services/ocr_vision.py), qui fonctionne en production :
    # BF16 sur Ampere+, FP32 sur Pascal (le FP16/BF16 émulé y est ~64x plus lent), lots réduits,
    # côté long de l'image limité à 1600 px, lignes triées, pas de mode formules.
    REC_BATCH = 4
    DET_BATCH = 1
    MAX_LONG_SIDE = 1600

    def load(self) -> None:
        import os

        import torch
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.layout import LayoutPredictor
        from surya.recognition import RecognitionPredictor

        if torch.cuda.is_available():
            device = "cuda"
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported(including_emulation=False) else torch.float32
        else:
            device, dtype = "cpu", torch.float32
        os.environ["TORCH_DEVICE"] = device
        os.environ.setdefault("RECOGNITION_BATCH_SIZE", str(self.REC_BATCH))
        os.environ.setdefault("DETECTOR_BATCH_SIZE", str(self.DET_BATCH))

        foundation = FoundationPredictor(device=device, dtype=dtype)
        foundation.encoder_chunk_size = 4096
        self._rec = RecognitionPredictor(foundation)
        self._det = DetectionPredictor(device=device, dtype=dtype)
        self._layout = LayoutPredictor(device=device, dtype=dtype)

    def _fit(self, image: Image.Image) -> Image.Image:
        long_side = max(image.size)
        if long_side <= self.MAX_LONG_SIDE:
            return image
        r = self.MAX_LONG_SIDE / long_side
        return image.resize((round(image.width * r), round(image.height * r)), Image.LANCZOS)

    def run(self, image: Image.Image) -> PageResult:
        image = self._fit(image)
        layout = self._layout([image])[0]
        ocr = self._rec(
            [image], det_predictor=self._det, sort_lines=True, math_mode=False,
            recognition_batch_size=self.REC_BATCH, detection_batch_size=self.DET_BATCH,
        )[0]
        boxes = sorted(layout.bboxes, key=lambda b: b.position)
        texts: list[list[str]] = [[] for _ in boxes]
        confs: list[list[float]] = [[] for _ in boxes]
        orphans: list[tuple[str, float]] = []
        for line in ocr.text_lines:
            x0, y0, x1, y1 = line.bbox
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            for i, b in enumerate(boxes):
                bx0, by0, bx1, by1 = b.bbox
                if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                    texts[i].append(line.text)
                    confs[i].append(float(line.confidence or 0))
                    break
            else:
                orphans.append((line.text, float(line.confidence or 0)))
        blocks = [
            Block(b.label, html_to_text(" ".join(t)), (sum(c) / len(c)) if c else None, tuple(b.bbox))
            for b, t, c in zip(boxes, texts, confs)
        ]
        if orphans:
            blocks.append(Block("Unassigned", html_to_text(" ".join(t for t, _ in orphans)),
                                sum(c for _, c in orphans) / len(orphans)))
        return PageResult(blocks)

    def close(self) -> None:
        for attr in ("_det", "_rec", "_layout"):
            if hasattr(self, attr):
                delattr(self, attr)
        try:
            import gc

            import torch

            gc.collect()
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001 - nettoyage au mieux
            pass


class Surya2Backend:
    """Surya 2 (signatures vérifiées contre surya-ocr 0.22.1 ; à lancer depuis .venv-surya2).

        manager = SuryaInferenceManager()
        layouts = LayoutPredictor(manager)([img])
        pages   = RecognitionPredictor(manager)([img], layouts)   # PageOCRResult.blocks[*].{label, html, bbox, confidence}

    Surya 2 choisit vLLM (Docker) dès qu'un GPU NVIDIA est vu. Pour llama.cpp (cette machine), il faut
    exporter SURYA_INFERENCE_BACKEND=llamacpp et SURYA_INFERENCE_PARALLEL=1 avant de lancer ; les fichiers
    GGUF sont pris dans le cache Hugging Face (datalab-to/surya-ocr-2-gguf) et `llama-server` : dossier Release donné par LLAMA_CPP_DIR (sinon PATH).
    LLAMA_CPP_NGL=0 force le CPU.
    """

    name = "surya2"

    def load(self) -> None:
        import os
        from pathlib import Path

        # Paramètre unique : LLAMA_CPP_DIR = dossier Release de llama.cpp (contenant llama-server.exe).
        # À fixer avant l'import de surya, qui lit LLAMA_CPP_BINARY au chargement de ses réglages.
        llama_dir = os.environ.get("LLAMA_CPP_DIR")
        if llama_dir and not os.environ.get("LLAMA_CPP_BINARY"):
            exe = Path(llama_dir) / "llama-server.exe"
            if not exe.is_file():
                raise FileNotFoundError("llama-server.exe introuvable dans LLAMA_CPP_DIR")
            os.environ["LLAMA_CPP_BINARY"] = str(exe)

        from surya.inference import SuryaInferenceManager
        from surya.layout import LayoutPredictor
        from surya.recognition import RecognitionPredictor

        self._manager = SuryaInferenceManager()
        self._layout = LayoutPredictor(self._manager)
        self._rec = RecognitionPredictor(self._manager)
        self._manager.start()  # démarre llama-server maintenant : le temps de chargement est mesuré à part

    def run(self, image: Image.Image) -> PageResult:
        layouts = self._layout([image])
        pred = self._rec([image], layouts)[0]
        blocks = []
        for b in pred.blocks:
            blocks.append(Block(b.label, html_to_text(b.html or ""),
                                float(b.confidence) if b.confidence is not None else None,
                                tuple(b.bbox) if getattr(b, "bbox", None) else None))
        return PageResult(blocks)

    def close(self) -> None:
        try:
            self._manager.stop()
        except Exception:  # noqa: BLE001
            pass


BACKENDS: dict[str, type] = {
    "dummy": DummyBackend,
    "surya1": Surya1Backend,
    "surya2": Surya2Backend,
}
