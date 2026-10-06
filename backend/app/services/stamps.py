"""Détection complémentaire des tampons et signatures (PLAN.md §7.6) : couleur d'encre (bleu / rouge en HSV).

Les tampons sont des zones colorées compactes ; on les isole par composantes connexes, puis on regroupe celles qui
sont proches (un tampon est fait de plusieurs traits). Les boîtes qui recouvrent déjà un bloc visuel détecté sont ignorées.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class StampBox:
    bbox: tuple[float, float, float, float]
    color: str  # bleu | rouge
    overlaps_text: bool = False


def _ink_mask(hsv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, s, v = cv2.split(hsv)
    sat = (s > 70) & (v > 40)
    blue = sat & (h >= 95) & (h <= 135)
    red = sat & ((h <= 8) | (h >= 170))
    return blue.astype(np.uint8) * 255, red.astype(np.uint8) * 255


def _iou_contains(a, b) -> float:
    """Part de la boîte `a` couverte par `b`."""
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return inter / area


def detect_stamps(bgr: np.ndarray, visual_boxes: list[tuple], text_boxes: list[tuple]) -> list[StampBox]:
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    found: list[StampBox] = []
    min_area = 0.0008 * w * h  # ignore le bruit et les petits mots colorés
    for name, mask in zip(("bleu", "rouge"), _ink_mask(hsv)):
        if cv2.countNonZero(mask) < 80:
            continue
        k = max(15, int(min(w, h) * 0.025))
        merged = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        n, _, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            ink = cv2.countNonZero(mask[y:y + bh, x:x + bw])
            if bw * bh < min_area or ink < 120:
                continue
            if bw > 0.8 * w or bh > 0.5 * h:  # trop grand : fond coloré, pas un tampon
                continue
            if ink / max(1, bw * bh) > 0.85:  # aplat plein : bandeau, pas un tampon
                continue
            box = (float(x), float(y), float(x + bw), float(y + bh))
            if any(_iou_contains(box, v) > 0.6 for v in visual_boxes):
                continue
            overlaps = any(_iou_contains(t, box) > 0.15 or _iou_contains(box, t) > 0.15 for t in text_boxes)
            found.append(StampBox(box, name, overlaps))
    return found
