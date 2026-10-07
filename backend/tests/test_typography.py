import cv2
import numpy as np

from app.services.typography import analyze_block, mark_bold


def _render(lines, scale=1.0, thickness=2, width=1200, align="left"):
    img = np.full((60 + 60 * len(lines), width, 3), 255, np.uint8)
    for k, t in enumerate(lines):
        (tw, _), _ = cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        x = 20 if align == "left" else (width - tw) // 2
        cv2.putText(img, t, (x, 60 + 60 * k), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def test_lignes_taille_et_centrage():
    gray = _render(["Une ligne", "Deux lignes ici", "Trois"], align="center")
    st = analyze_block(gray, (0, 0, gray.shape[1], gray.shape[0]), 200)
    assert st["lines"] == 3
    # texte de ~30 px de haut (interligne double, donc mesuré sur l'encre) à 200 dpi ≈ 11 pt
    assert 9 < st["pt"] < 14
    assert st["align"] == "center"


def test_gras_relatif():
    normal = _render(["texte normal de la page"] * 3, thickness=1)
    gras = _render(["texte en gras de la page"] * 3, thickness=4)
    styles = [analyze_block(g, (0, 0, g.shape[1], g.shape[0]), 200) for g in (normal, normal, gras)]
    mark_bold(styles)
    assert [s["bold"] for s in styles] == [False, False, True]


def test_bloc_vide():
    blank = np.full((100, 300), 255, np.uint8)
    assert analyze_block(blank, (0, 0, 300, 100), 200) is None
