"""Dégrade artificiellement des documents NON confidentiels pour fabriquer des échantillons de test.

Entrée : PDF (rendus via pypdfium2) ou images (png/jpg/tif/bmp), fichier ou dossier.
Sortie : une image PNG par page dans le dossier de sortie, nommée <nom>_pNNN.png.

Profils : leger | delave | tres_delave. La dégradation est reproductible (--seed).
Ne jamais utiliser ce script sur des documents confidentiels (voir PLAN.md §4 bis).

    python tools/degrade.py samples/originaux samples/degrades --profil tres_delave --seed 7
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


@dataclass(frozen=True)
class Profile:
    fade: float  # 0 = encre intacte, 1 = encre invisible (mélange avec le papier)
    skew_deg: float  # inclinaison maximale (±)
    blur_sigma: float
    noise_sigma: float
    speckle_density: float  # fraction de pixels tachés
    illum_strength: float  # éclairage inégal (0..1)
    jpeg_quality: int
    stamps: int  # nombre de tampons factices


PROFILES: dict[str, Profile] = {
    "leger": Profile(0.15, 0.8, 0.6, 3.0, 0.0002, 0.10, 85, 0),
    "delave": Profile(0.45, 1.5, 1.0, 6.0, 0.0006, 0.25, 70, 1),
    "tres_delave": Profile(0.70, 2.5, 1.4, 9.0, 0.0012, 0.40, 55, 2),
}


def spread(n: int, k: int) -> list[int]:
    """k indices répartis régulièrement sur n pages (le premier est toujours la page 0)."""
    if k <= 0 or k >= n:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) if k > 1 else 0 for i in range(k)})


def load_images(path: Path, dpi: int, pages_per_pdf: int = 0, suffix_images: bool = True) -> list[tuple[str, np.ndarray]]:
    """Retourne [(nom_de_page, image BGR)] pour un fichier ou un dossier.

    pages_per_pdf > 0 : ne prélève que ce nombre de pages, réparties sur tout le PDF.
    suffix_images=False : une image garde son nom (sinon elle reçoit « _p001 », comme une page de PDF)."""
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    out: list[tuple[str, np.ndarray]] = []
    for f in files:
        ext = f.suffix.lower()
        if ext == ".pdf":
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(f))
            for i in spread(len(pdf), pages_per_pdf):
                pil = pdf[i].render(scale=dpi / 72).to_pil().convert("RGB")
                out.append((f"{f.stem}_p{i + 1:03d}", cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)))
        elif ext in IMAGE_EXT:
            img = cv2.imdecode(np.fromfile(str(f), dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                out.append((f"{f.stem}_p001" if suffix_images else f.stem, img))
    return out


def _rotate(img: np.ndarray, angle: float, fill: tuple[int, int, int]) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=fill)


def _illumination(h: int, w: int, rng: np.random.Generator, strength: float) -> np.ndarray:
    """Carte de luminosité multiplicative : dégradé linéaire + vignettage."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ang = rng.uniform(0, 2 * np.pi)
    grad = (np.cos(ang) * (xx / w - 0.5) + np.sin(ang) * (yy / h - 0.5))
    vignette = ((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2)
    return 1.0 - strength * (0.6 * (grad + 0.5) * 0.5 + 0.8 * vignette)


def _fake_stamp(img: np.ndarray, rng: np.random.Generator) -> None:
    """Tampon circulaire factice (bleu ou rouge) avec texte latin générique, semi-transparent."""
    h, w = img.shape[:2]
    r = int(min(h, w) * rng.uniform(0.06, 0.11))
    cx = int(rng.uniform(r + 5, w - r - 5))
    cy = int(rng.uniform(r + 5, h - r - 5))
    color = (200, 60, 30) if rng.random() < 0.6 else (40, 40, 200)  # BGR : bleu / rouge
    layer = np.zeros_like(img)
    cv2.circle(layer, (cx, cy), r, color, max(2, r // 14), cv2.LINE_AA)
    cv2.circle(layer, (cx, cy), int(r * 0.78), color, max(1, r // 28), cv2.LINE_AA)
    scale = r / 90
    for text, dy in (("TAMPON", -0.18), ("TEST", 0.22)):
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, int(scale * 2)))
        cv2.putText(layer, text, (cx - tw // 2, int(cy + dy * r + th / 2)), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, color, max(1, int(scale * 2)), cv2.LINE_AA)
    layer = _rotate(layer, rng.uniform(-25, 25), (0, 0, 0))
    alpha = rng.uniform(0.45, 0.75)
    mask = (layer.sum(axis=2) > 0)[..., None]
    img[:] = np.where(mask, (img * (1 - alpha) + layer * alpha).astype(np.uint8), img)


def _fake_signature(img: np.ndarray, rng: np.random.Generator) -> None:
    """Gribouillis de type signature (courbe lissée, encre bleue)."""
    h, w = img.shape[:2]
    x0 = int(rng.uniform(0.1, 0.6) * w)
    y0 = int(rng.uniform(0.6, 0.9) * h)
    n = 14
    xs = x0 + np.cumsum(rng.uniform(5, 30, n)) * (w / 1000)
    ys = y0 + rng.normal(0, 25, n) * (h / 1000)
    pts = np.stack([xs, ys], axis=1).astype(np.int32)
    layer = img.copy()
    cv2.polylines(layer, [pts], False, (170, 50, 20), max(2, w // 600), cv2.LINE_AA)
    img[:] = cv2.addWeighted(layer, 0.7, img, 0.3, 0)


def degrade(img: np.ndarray, p: Profile, rng: np.random.Generator, stamps: bool = True) -> np.ndarray:
    h, w = img.shape[:2]
    paper = np.array([rng.uniform(225, 245), rng.uniform(232, 248), rng.uniform(240, 252)])  # BGR : papier légèrement jauni
    out = img.astype(np.float32)

    # Encre délavée : mélange vers la couleur du papier. Le blanc devient la teinte du papier.
    out = out / 255.0 * paper
    ink_keep = 1.0 - p.fade
    out = paper - (paper - out) * ink_keep

    out = out * _illumination(h, w, rng, p.illum_strength)[..., None]
    out = np.clip(out, 0, 255).astype(np.uint8)

    if stamps:
        for _ in range(p.stamps):
            _fake_stamp(out, rng)
        if p.stamps:
            _fake_signature(out, rng)

    out = _rotate(out, rng.uniform(-p.skew_deg, p.skew_deg), tuple(int(c) for c in paper))

    if p.blur_sigma > 0:
        out = cv2.GaussianBlur(out, (0, 0), p.blur_sigma)

    noise = rng.normal(0, p.noise_sigma, out.shape).astype(np.float32)
    out = np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    if p.speckle_density > 0:
        n = int(h * w * p.speckle_density)
        ys, xs = rng.integers(0, h, n), rng.integers(0, w, n)
        out[ys, xs] = rng.integers(40, 140, (n, 1))

    ok, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, p.jpeg_quality])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR) if ok else out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entree", type=Path, help="PDF, image ou dossier de documents NON confidentiels")
    ap.add_argument("sortie", type=Path, help="dossier de sortie (créé si besoin)")
    ap.add_argument("--profil", choices=sorted(PROFILES), default="delave")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dpi", type=int, default=200, help="résolution de rendu des PDF")
    ap.add_argument("--sans-tampons", action="store_true")
    ap.add_argument("--pages-par-pdf", type=int, default=0, help="prélever N pages réparties par PDF (0 = toutes)")
    args = ap.parse_args(argv)

    if not args.entree.exists():
        print(f"Entrée introuvable : {args.entree}", file=sys.stderr)
        return 2
    pages = load_images(args.entree, args.dpi, args.pages_par_pdf)
    if not pages:
        print("Aucune page lisible.", file=sys.stderr)
        return 2
    args.sortie.mkdir(parents=True, exist_ok=True)
    profile = PROFILES[args.profil]
    for i, (name, img) in enumerate(pages):
        rng = np.random.default_rng([args.seed, i])
        res = degrade(img, profile, rng, stamps=not args.sans_tampons)
        ok, buf = cv2.imencode(".png", res)
        if not ok:
            print(f"Échec d'encodage : {name}", file=sys.stderr)
            return 1
        (args.sortie / f"{name}.png").write_bytes(buf.tobytes())
    print(f"{len(pages)} page(s) dégradée(s) (profil {args.profil}) -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
