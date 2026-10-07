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
    # Un tampon est fait de plusieurs traits (anneau, texte) : on écarte les boîtes contenues dans une plus grande.
    found.sort(key=lambda s: -(s.bbox[2] - s.bbox[0]) * (s.bbox[3] - s.bbox[1]))
    kept: list[StampBox] = []
    for s in found:
        if not any(_iou_contains(s.bbox, k.bbox) > 0.9 for k in kept):
            kept.append(s)
    return kept


def erase_stamps(bgr: np.ndarray, max_aspect: float = 3.0) -> tuple[np.ndarray, list[StampBox]]:
    """Copie de la page où l'encre bleue / rouge des tampons est remplacée par le papier.

    Sert uniquement à la LECTURE : un tampon posé sur du texte le rend illisible pour le modèle (« JOURNAL OFFICE
    PAMPON ») ou le fait classer comme image (texte jamais lu). L'image d'origine, tampons compris, reste celle des
    découpes et du Word. Seules les zones compactes sont effacées (tampons, signatures) : une ligne manuscrite
    allongée au stylo bleu (formulaire rempli) n'est pas touchée.
    """
    stamps = [s for s in detect_stamps(bgr, [], [])
              if (s.bbox[2] - s.bbox[0]) <= max_aspect * (s.bbox[3] - s.bbox[1])
              and (s.bbox[3] - s.bbox[1]) <= max_aspect * (s.bbox[2] - s.bbox[0])]
    if not stamps:
        return bgr, []
    zone = np.zeros(bgr.shape[:2], bool)
    for s in stamps:
        x0, y0, x1, y1 = (int(v) for v in s.bbox)
        zone[y0:y1, x0:x1] = True
    # Encre colorée, bords pâles compris : un canal nettement au-dessus des deux autres.
    b_, g_, r_ = (bgr[..., i].astype(np.int16) for i in range(3))
    tint = np.maximum(b_ - np.maximum(g_, r_), r_ - np.maximum(g_, b_))
    mask = (tint > 18) & zone
    if not mask.any():
        return bgr, stamps
    # Bords anticrénelés (mi-encre, mi-papier) : sinon un contour gris du tampon reste visible.
    mask = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & zone
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    out = bgr.copy()
    # Papier : teinte médiane des pixels clairs non colorés de la zone.
    plain = zone & ~mask & (gray > np.percentile(gray[zone], 50))
    paper = np.median(bgr[plain], axis=0).astype(np.float32) if plain.any() else np.full(3, 255, np.float32)
    # Une encre colorée laisse passer sa propre couleur : dans le canal le plus clair (corrigé de la teinte du
    # papier), le tampon disparaît presque, alors que le texte noir qu'il recouvre reste sombre. Pas de trous dans
    # les lettres, contrairement à un simple remplacement par le papier.
    px = bgr[mask].astype(np.float32) / np.maximum(paper, 1)
    level = float(paper.mean())
    v = np.clip(px.max(axis=1) * level, 0, 255)
    pale = v > 0.75 * level  # trace pâle du tampon → papier ; le texte recouvert reste sombre
    fill = np.repeat(v[:, None], 3, axis=1).astype(np.uint8)
    fill[pale] = paper.astype(np.uint8)
    out[mask] = fill
    return out, stamps
