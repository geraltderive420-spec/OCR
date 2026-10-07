"""Moteurs OCR interchangeables. Le pipeline ne dépend que de `OcrEngine` et des blocs normalisés."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image


@dataclass
class OcrBlock:
    label: str  # Table, Picture, Figure, Text, SectionHeader, PageHeader, PageFooter, ListItem, Caption...
    html: str  # texte ou HTML (tableaux)
    bbox: tuple[float, float, float, float]  # pixels de l'image fournie au moteur
    confidence: float | None = None
    error: bool = False


@dataclass
class OcrPage:
    blocks: list[OcrBlock] = field(default_factory=list)


class OcrEngine(Protocol):
    name: str

    def start(self) -> None: ...
    def recognize(self, image: Image.Image) -> OcrPage: ...
    def stop(self) -> None: ...


class OcrConfigError(RuntimeError):
    """Le moteur OCR est mal configuré (binaire ou modèles introuvables) : le document échoue avec un message clair."""


class OcrUnavailable(RuntimeError):
    """Le moteur ne peut pas démarrer maintenant (ex. VRAM insuffisante) : le job doit attendre."""


VISUAL_LABELS = {"Picture", "Figure", "Diagram", "Image"}
TABLE_LABELS = {"Table"}
