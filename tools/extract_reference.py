"""Extrait la couche texte native de PDF publics pour servir de transcription de référence.

Pour chaque page prélevée comme dans degrade.py (--pages-par-pdf), écrit <nom>_pNNN.txt
(même nom que les images dégradées) si la page porte une vraie couche de texte.
Les PDF scannés sans couche texte sont ignorés : leurs pages n'ont pas de référence.
À réserver aux documents NON confidentiels.

    python tools/extract_reference.py samples/originaux samples/reference --pages-par-pdf 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pypdfium2 as pdfium

sys.path.insert(0, str(Path(__file__).parent))
from degrade import spread  # noqa: E402

MIN_CHARS = 200  # en dessous : page sans vraie couche texte (ou page quasi vide)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entree", type=Path)
    ap.add_argument("sortie", type=Path)
    ap.add_argument("--pages-par-pdf", type=int, default=0)
    args = ap.parse_args(argv)

    args.sortie.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for f in sorted(args.entree.glob("*.pdf")):
        pdf = pdfium.PdfDocument(str(f))
        for i in spread(len(pdf), args.pages_par_pdf):
            text = pdf[i].get_textpage().get_text_range().strip()
            if len(text) < MIN_CHARS:
                skipped += 1
                continue
            (args.sortie / f"{f.stem}_p{i + 1:03d}.txt").write_text(text, encoding="utf-8")
            written += 1
    print(f"{written} référence(s) écrite(s), {skipped} page(s) sans couche texte -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
