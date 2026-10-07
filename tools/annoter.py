"""Dessine la mise en page détectée sur vos propres scans, pour repérer les erreurs de détection.

Tout reste sur ce poste : les images annotées sont écrites dans le dossier de sortie, rien n'est envoyé.
Couleurs : vert = texte, bleu = titre, orange = en-tête / pied, rouge = tableau, violet = image,
magenta = tampon ou signature repéré par la couleur de l'encre. Le numéro est l'ordre de lecture.

    cd backend
    ..\\.venv-surya2\\Scripts\\python.exe ..\\tools\\annoter.py MON_SCAN.pdf ..\\annotations
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

COLORS = {"Text": (0, 160, 0), "SectionHeader": (200, 0, 0), "PageHeader": (0, 140, 230), "PageFooter": (0, 140, 230),
          "Table": (0, 0, 220), "Picture": (200, 0, 200), "Figure": (200, 0, 200), "Stamp": (255, 0, 255)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entree", type=Path, help="PDF, image ou dossier")
    ap.add_argument("sortie", type=Path, help="dossier des images annotées")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args(argv)

    os.chdir(ROOT / "backend")  # lit backend/.env (OCR_LLAMA_CPP_DIR…)
    import cv2
    from PIL import Image

    from degrade import load_images
    from app.services import preprocess
    from app.services.ocr.surya2 import Surya2Engine
    from app.services.stamps import detect_stamps

    args.sortie.mkdir(parents=True, exist_ok=True)
    eng = Surya2Engine()
    eng.start()
    try:
        for rank, (_name, bgr) in enumerate(load_images(args.entree, args.dpi, suffix_images=False), start=1):
            color, rgb, *_ = preprocess.process_page(bgr, "auto")
            page = eng.recognize(Image.fromarray(rgb))
            vis = color.copy()
            visual = [b.bbox for b in page.blocks if b.label in ("Picture", "Figure")]
            text = [b.bbox for b in page.blocks if b.label not in ("Picture", "Figure")]
            stamps = [("Stamp", s.bbox) for s in detect_stamps(color, visual, text)]
            for k, (label, bbox) in enumerate([(b.label, b.bbox) for b in page.blocks] + stamps):
                x0, y0, x1, y1 = (int(v) for v in bbox)
                c = COLORS.get(label, (90, 90, 90))
                cv2.rectangle(vis, (x0, y0), (x1, y1), c, 3)
                cv2.putText(vis, f"{k} {label}", (x0 + 3, max(18, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, 2)
            out = args.sortie / f"page_{rank:03d}.jpg"
            cv2.imwrite(str(out), vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
            print(f"page {rank} : {len(page.blocks)} blocs, {len(stamps)} tampon(s) -> {out.name}", flush=True)
    finally:
        eng.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
