"""Analyse typographique d'un bloc de texte sur l'image d'origine (aucun contenu, seulement des mesures).

Le moteur OCR ne donne ni taille de police, ni graisse, ni alignement : on les mesure sur l'image, ligne par ligne.
- lignes de texte par profil de projection horizontal ; filets de soulignement repérés et mis à part ;
- taille : hauteur « ligne de base → haut des capitales / ascendantes » de chaque ligne. Contrairement à la hauteur
  d'encre totale, elle ne dépend pas de la présence de jambages (« site Bir El Kelb » n'en a pas) ni de l'interligne
  (1 ou 1,5) ; la conversion en points dépend de la famille de police (faite à l'export) ;
- épaisseur relative des traits (gras par comparaison avec le corps du texte de la page) ;
- alignement (justifié, centré), retrait de première ligne (positif) ou suspendu (négatif), largeur de chaque ligne
  (pour reproduire les retours à la ligne des titres, adresses et signatures).
"""
from __future__ import annotations

import cv2
import numpy as np

# Hauteur « ligne de base → haut des capitales et ascendantes » en fraction du corps (em), accents compris.
CAP_TO_EM = {"sans": 0.75, "serif": 0.69, "arabic": 0.62}  # arabe : hauteur de l'alif / du lam au-dessus de la ligne
DEFAULT_FAMILY = "sans"


def _binarize(gray: np.ndarray) -> np.ndarray:
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return bw


def _runs(profile: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """Intervalles [début, fin) où le profil est non nul, fusionnés à travers les trous de 1 pixel."""
    on = profile > 0
    out: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(on)))
    merged: list[tuple[int, int]] = []
    for a, b in out:
        if merged and a - merged[-1][1] <= 1:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if b - a >= min_len]


def _longest_run(row: np.ndarray) -> int:
    """Plus longue suite continue de pixels d'encre d'une rangée."""
    if not row.any():
        return 0
    padded = np.concatenate(([0], row.astype(np.int8), [0]))
    d = np.diff(padded)
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return int((ends - starts).max())


def pt_from_cap(cap_px: float, dpi: int, family: str = DEFAULT_FAMILY) -> float:
    return cap_px / CAP_TO_EM.get(family, CAP_TO_EM[DEFAULT_FAMILY]) * 72 / dpi


def analyze_block(gray: np.ndarray, bbox, dpi: int, direction: str = "ltr") -> dict | None:
    """Mesures d'un bloc de texte, ou None si illisible :
    {'cap_px', 'pt', 'lines', 'stroke', 'align', 'indent', 'line_w', 'underline'}."""
    h, w = gray.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in bbox)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 10 or y1 - y0 < 6:
        return None
    ink = _binarize(gray[y0:y1, x0:x1]) > 0
    width = x1 - x0
    cols = np.flatnonzero(ink.any(axis=0))
    if cols.size == 0:
        return None
    span = cols[-1] - cols[0] + 1

    # Filets (soulignements, traits) : rangées faites d'une seule longue suite d'encre. Mis à part avant la mesure.
    row_ink = ink.sum(axis=1)
    rule = np.array([r > 0 and _longest_run(ink[i]) >= max(0.3 * span, 20) and _longest_run(ink[i]) >= 0.8 * r
                     for i, r in enumerate(row_ink)])
    text = ink.copy()
    text[rule] = False
    raw = text.sum(axis=1)

    core = raw.copy()
    core[core < max(2, 0.01 * width)] = 0
    min_line = max(3, int(dpi * 0.02))  # ~1,5 pt : en dessous, ce n'est pas une ligne de texte
    cores = _runs(core, 1)
    if not cores:
        return None
    med = float(np.median([b - a for a, b in cores]))
    cores = [(a, b) for a, b in cores if b - a >= max(min_line, 0.4 * med)]  # points, accents, bruit
    if not cores:
        return None

    lines = []  # (haut, bas, cap_px, gauche, droite, souligné)
    for k, (a, b) in enumerate(cores):
        lo = cores[k - 1][1] if k else 0
        hi = cores[k + 1][0] if k + 1 < len(cores) else len(raw)
        while a > lo and raw[a - 1] > 0:
            a -= 1
        while b < hi and raw[b] > 0:
            b += 1
        prof = raw[a:b].astype(float)
        peak = prof.max()
        base = max(i for i in range(len(prof)) if prof[i] >= 0.35 * peak)
        top = min(i for i in range(len(prof)) if prof[i] >= 0.03 * peak)
        c = np.flatnonzero(text[a:b].any(axis=0))
        under = bool(rule[b:min(len(rule), b + max(4, (b - a) // 2))].any())
        lines.append((a, b, base - top + 1, c[0] / width, (c[-1] + 1) / width, under))

    caps = sorted(ln[2] for ln in lines)
    cap_px = float(caps[len(caps) // 2])  # médiane haute : un mot plus gros (« Objet ») ne fausse pas le bloc

    # Épaisseur des traits (filets exclus), rapportée à la hauteur des capitales.
    dist = cv2.distanceTransform(text.astype(np.uint8), cv2.DIST_L2, 3)
    ridge = dist[(dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8))) & (dist > 0)]
    stroke = float(2 * np.median(ridge)) / max(1.0, cap_px) if ridge.size else None

    lefts = [ln[3] for ln in lines]
    rights = [ln[4] for ln in lines]
    align = None
    if len(lines) >= 3:
        body = lines[:-1]  # la dernière ligne d'un paragraphe justifié est courte
        full = sum(1 for ln in body if ln[3] < 0.06 and ln[4] > 0.97)
        if full >= 0.7 * len(body):
            align = "justify"
    if align is None and len(lines) >= 2:
        centers = [(l + r) / 2 for l, r in zip(lefts, rights)]
        if max(centers) - min(centers) < 0.03 and max(lefts) - min(lefts) > 0.03:
            align = "center"
        elif max(rights) - min(rights) < 0.02 and max(lefts) - min(lefts) > 0.05 and direction == "ltr":
            align = "right"

    # Retrait de 1re ligne (> 0) ou suspendu (< 0), mesuré du côté où le texte commence.
    indent = 0.0
    if len(lines) >= 2 and align != "center":
        starts = lefts if direction == "ltr" else [1 - r for r in rights]
        indent = starts[0] - float(np.median(starts[1:]))
        if abs(indent) < 0.04:  # en dessous : bruit de mesure (ligne abîmée), pas un vrai retrait
            indent = 0.0

    centers = [(ln[0] + ln[1]) / 2 for ln in lines]
    pitch = float(np.median(np.diff(centers))) if len(lines) >= 2 else None
    return {
        "cap_px": round(cap_px, 1),
        "pitch_px": round(pitch, 1) if pitch else None,  # distance d'une ligne à la suivante (interligne)
        "pt": round(pt_from_cap(cap_px, dpi, "arabic" if direction == "rtl" else DEFAULT_FAMILY), 2),
        "lines": len(lines),
        "stroke": round(stroke, 4) if stroke else None,
        "align": align,
        "indent": round(float(indent), 3),
        "line_w": [round(float(r - l), 3) for l, r in zip(lefts, rights)],
        "underline": bool(sum(ln[5] for ln in lines) * 2 > len(lines)),
    }


def mark_bold(styles: list[dict | None], weights: list[int] | None = None, ratio: float = 1.25) -> None:
    """Gras relatif : trait nettement plus épais que celui du corps du texte de la page.

    La référence est la médiane pondérée par la longueur du texte : le corps (beaucoup de caractères) l'emporte
    sur les titres et en-têtes en gras, contrairement à une médiane simple des blocs.
    """
    weights = weights or [1] * len(styles)
    pairs = sorted((s["stroke"], wt) for s, wt in zip(styles, weights) if s and s.get("stroke"))
    if len(pairs) < 2:
        return
    half, acc, ref = sum(wt for _, wt in pairs) / 2, 0, pairs[-1][0]
    for st, wt in pairs:
        acc += wt
        if acc >= half:
            ref = st
            break
    for s in styles:
        if s and s.get("stroke"):
            s["bold"] = bool(s["stroke"] > ratio * ref)
