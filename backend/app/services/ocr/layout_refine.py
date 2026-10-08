"""Recalage de la mise en page sur les lignes de texte détectées.

Le modèle de mise en page de Surya 2 donne des boîtes en coordonnées 0-1000 avec une erreur de quelques
pourcents ; Surya ne découpe qu'avec 4 px de marge. Une boîte décalée rogne le début des lignes (à droite pour
l'arabe) ou une ligne entière, et du texte peut n'être couvert par aucune boîte. Le détecteur de lignes de Surya
(modèle léger, sur processeur) localise précisément chaque ligne : on agrandit chaque bloc de texte jusqu'aux lignes
qui lui appartiennent et on crée des blocs pour les lignes orphelines.

Fonctions pures sur des boîtes (x0, y0, x1, y1) : testables sans modèle.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Box = tuple[float, float, float, float]

# Blocs dont on recale les bords sur les lignes (les tableaux et images gardent leur boîte).
REFINE_LABELS = {"Text", "SectionHeader", "PageHeader", "PageFooter", "ListGroup", "ListItem", "Caption", "Footnote",
                 "Form", "TableOfContents", "Bibliography", "Code"}
# Blocs qui « couvrent » leurs lignes sans être recalés.
OPAQUE_LABELS = {"Table", "Picture", "Figure", "Diagram", "Image", "Equation", "ChemicalBlock"}


@dataclass
class RefinedBlock:
    label: str
    bbox: Box
    source: int | None  # indice du bloc d'origine, None pour un bloc créé à partir de lignes orphelines
    lines: list[Box] = field(default_factory=list)


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a: Box, b: Box) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def union(a: Box, b: Box) -> Box:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _pad(b: Box, px: float, w: float, h: float) -> Box:
    return max(0.0, b[0] - px), max(0.0, b[1] - px), min(w, b[2] + px), min(h, b[3] + px)


def refine(blocks: list[tuple[str, Box]], lines: list[Box], width: float, height: float,
           min_share: float = 0.3, see_through: set[int] | None = None) -> list[RefinedBlock]:
    """Recale les blocs sur les lignes et ajoute les groupes de lignes orphelines (dans l'ordre de lecture).

    - une ligne appartient au bloc de texte qui en couvre la plus grande part (au moins `min_share`) ;
    - un bloc s'étend à l'union de ses lignes, sauf si cela le fait empiéter sur un autre bloc ;
    - une ligne couverte par un tableau ou une image est ignorée, sauf si l'image est dans `see_through`
      (tampon posé sur du texte : les lignes données ici sont celles du texte noir sous le tampon) ;
    - les lignes restantes, regroupées par proximité, deviennent des blocs « Text » insérés après le bloc
      précédent de la même colonne.
    """
    lines = [ln for ln in lines if area(ln) > 0]
    hs = sorted(ln[3] - ln[1] for ln in lines)
    line_h = hs[len(hs) // 2] if hs else 20.0
    pad = max(2.0, 0.15 * line_h)

    out = [RefinedBlock(label, tuple(map(float, bbox)), i) for i, (label, bbox) in enumerate(blocks)]
    see_through = see_through or set()
    opaque = [b for b in out if b.label in OPAQUE_LABELS and b.source not in see_through]
    orphans: list[Box] = []
    for ln in lines:
        la = area(ln)
        if any(inter(ln, b.bbox) / la >= 0.5 for b in opaque):
            continue
        best, share = None, 0.0
        for b in out:
            if b.label in REFINE_LABELS:
                s = inter(ln, b.bbox) / la
                if s > share:
                    best, share = b, s
        if best is not None and share >= min_share:
            best.lines.append(ln)
        elif not any(inter(ln, b.bbox) / la >= min_share for b in out
                     if b.label in REFINE_LABELS or b in opaque):
            orphans.append(ln)

    # Agrandissement : union des lignes du bloc, refusé s'il recouvre un autre bloc (colonnes voisines, tableau).
    originals = [b.bbox for b in out]
    for k, b in enumerate(out):
        if not b.lines:
            continue
        grown = b.bbox
        for ln in b.lines:
            grown = union(grown, ln)
        grown = _pad(grown, pad, width, height)
        clash = any(inter(grown, o) > 0.15 * area(o) and inter(b.bbox, o) <= 0.15 * area(o)
                    for j, o in enumerate(originals) if j != k and area(o) > 0)
        if not clash:
            b.bbox = grown

    out = _split_wide_gaps(out, line_h, pad, width, height)

    for group in _group_lines(orphans, line_h):
        box = group[0]
        for ln in group[1:]:
            box = union(box, ln)
        new = RefinedBlock("Text", _pad(box, pad, width, height), None, group)
        out.insert(_insert_at(out, new.bbox), new)
    return out


def _split_wide_gaps(blocks: list[RefinedBlock], line_h: float, pad: float, width: float,
                     height: float) -> list[RefinedBlock]:
    """Un bloc d'une seule ligne fait de morceaux séparés par de grands blancs (« N° :  … réf …  : رقم »,
    « Sidi Bel Abbès, le   … سيدي بلعباس، في ») est coupé en blocs distincts, rendus côte à côte dans le Word.
    Un blanc entre mots ne dépasse pas ~0,3 corps : au-delà d'une hauteur de ligne, c'est une tabulation."""
    out: list[RefinedBlock] = []
    for b in blocks:
        lns = sorted(b.lines, key=lambda ln: ln[0])
        one_row = len(lns) >= 2 and all(
            min(ln[3], lns[0][3]) - max(ln[1], lns[0][1]) >= 0.5 * min(ln[3] - ln[1], lns[0][3] - lns[0][1]) for ln in lns)
        if b.label not in REFINE_LABELS or not one_row:
            out.append(b)
            continue
        h = sorted(ln[3] - ln[1] for ln in lns)[len(lns) // 2]
        parts: list[list[Box]] = [[lns[0]]]
        for ln in lns[1:]:
            right = max(x[2] for x in parts[-1])
            if ln[0] - right > max(h, 0.8 * line_h):
                parts.append([ln])
            else:
                parts[-1].append(ln)
        if len(parts) == 1:
            out.append(b)
            continue
        for part in parts:
            box = part[0]
            for ln in part[1:]:
                box = union(box, ln)
            out.append(RefinedBlock(b.label, _pad(box, pad, width, height), b.source, part))
    return out


def _group_lines(lines: list[Box], line_h: float) -> list[list[Box]]:
    """Regroupe les lignes voisines (écart vertical < 1 ligne, recouvrement horizontal ≥ 50 %)."""
    groups: list[list[Box]] = []
    for ln in sorted(lines, key=lambda b: (b[1], b[0])):
        for g in groups:
            last = g[-1]
            gap = ln[1] - last[3]
            ov = min(ln[2], last[2]) - max(ln[0], last[0])
            if -0.5 * line_h <= gap <= 1.0 * line_h and ov >= 0.5 * min(ln[2] - ln[0], last[2] - last[0]):
                g.append(ln)
                break
        else:
            groups.append([ln])
    return groups


def _insert_at(blocks: list[RefinedBlock], box: Box) -> int:
    """Position dans l'ordre de lecture : après le dernier bloc situé au-dessus dans la même colonne."""
    best = None
    for i, b in enumerate(blocks):
        ov = min(box[2], b.bbox[2]) - max(box[0], b.bbox[0])
        if b.bbox[1] <= box[1] and ov > 0.3 * min(box[2] - box[0], b.bbox[2] - b.bbox[0]):
            best = i
    if best is not None:
        return best + 1
    above = [i for i, b in enumerate(blocks) if b.bbox[3] <= box[1]]
    return (max(above) + 1) if above else 0


def fuse_detections(blocks: list[tuple[str, Box]], detections: list[tuple[str, Box, float]],
                    min_score: float = 0.6, max_covered: float = 0.3) -> tuple[list[tuple[str, Box, int | None]], int]:
    """Ajoute à la mise en page de Surya les zones qu'un détecteur (Heron) voit et que Surya a sautées.

    Une détection sûre (score ≥ `min_score`) dont moins de `max_covered` de la surface est couverte par les blocs de
    Surya devient un bloc, inséré dans l'ordre de lecture ; les détections déjà couvertes ne changent rien (Surya garde
    l'ordre et les étiquettes). Retourne ([(étiquette, boîte, indice Surya ou None)], nombre de blocs ajoutés).
    """
    out = [RefinedBlock(label, tuple(map(float, box)), i) for i, (label, box) in enumerate(blocks)]
    added = 0
    for label, box, score in sorted(detections, key=lambda d: -d[2]):
        a = area(box)
        if score < min_score or a <= 0:
            continue
        covered = sum(inter(box, b.bbox) for b in out) / a
        if covered >= max_covered:
            continue
        out.insert(_insert_at(out, box), RefinedBlock(label, tuple(map(float, box)), None))
        added += 1
    return [(b.label, b.bbox, b.source) for b in out], added


def token_estimate(lines: list[Box]) -> int:
    """Estimation du nombre de tokens d'un bloc créé (~4 tokens par hauteur de ligne de largeur)."""
    return int(sum(4 * (ln[2] - ln[0]) / max(1.0, ln[3] - ln[1]) for ln in lines)) + 50


def snap_to_ink(gray, box: Box, max_grow: float = 0.15) -> Box:
    """Agrandit la boîte d'une image (logo, tampon, photo) jusqu'aux traits qu'elle coupe.

    Les composantes d'encre qui touchent la boîte et tiennent entièrement dans une marge de `max_grow` × sa taille
    sont incluses ; celles qui sortent de la marge (cadre de page, filets de tableau, colonne de texte) sont ignorées.
    """
    import cv2
    import numpy as np

    h, w = gray.shape[:2]
    bw_, bh_ = box[2] - box[0], box[3] - box[1]
    if bw_ <= 4 or bh_ <= 4:
        return box
    mx, my = max_grow * bw_, max_grow * bh_
    rx0, ry0 = int(max(0, box[0] - mx)), int(max(0, box[1] - my))
    rx1, ry1 = int(min(w, box[2] + mx)), int(min(h, box[3] + my))
    region = gray[ry0:ry1, rx0:rx1]
    if region.size == 0:
        return box
    _, ink = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = cv2.dilate(ink, np.ones((5, 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    bx0, by0, bx1, by1 = box[0] - rx0, box[1] - ry0, box[2] - rx0, box[3] - ry0
    out = list(box)
    for i in range(1, n):
        x, y, cw, ch, _a = stats[i]
        if x <= 0 or y <= 0 or x + cw >= rx1 - rx0 or y + ch >= ry1 - ry0:
            continue  # sort de la marge : pas une partie de l'image
        if x + cw < bx0 or x > bx1 or y + ch < by0 or y > by1:
            continue  # ne touche pas la boîte
        if bx0 <= x and x + cw <= bx1 and by0 <= y and y + ch <= by1:
            continue  # déjà dedans
        out = [min(out[0], x + rx0), min(out[1], y + ry0), max(out[2], x + cw + rx0), max(out[3], y + ch + ry0)]
    return tuple(float(v) for v in out)


def has_printed_text(gray, box: Box, min_share: float = 0.01) -> bool:
    """Reste-t-il de l'encre (texte imprimé) dans la boîte d'une page dont les tampons ont été effacés ?"""
    import cv2
    import numpy as np

    x0, y0, x1, y1 = (int(round(v)) for v in box)
    crop = gray[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if crop.size == 0:
        return False
    paper = float(np.percentile(crop, 90))
    ink = crop < paper - 60  # nettement plus sombre que le papier
    ink = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))  # bruit isolé
    return float(ink.mean()) >= min_share
