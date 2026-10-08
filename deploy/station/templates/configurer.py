"""Configuration de la station : crée app\\backend\\.env (clé secrète aléatoire) s'il n'existe pas, sans jamais l'écraser.

Les chemins (données, llama.cpp, modèles) ne sont PAS écrits dans .env : ocr_env.bat les dérive de l'emplacement du
colis, qui reste ainsi déplaçable. Les ports et adresses d'écoute non plus : ils viennent de ocr_env.bat et des valeurs
par défaut de l'application. .env ne garde que ce qui est propre à ce poste : la clé de signature des sessions et
quelques réglages. Les autres réglages (largeur des pages, slots...) se changent dans Administration > Moteur OCR.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

KIT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
ENV = KIT / "app" / "backend" / ".env"

TEMPLATE = """# Genere par configurer.py. Propre a ce poste : ne pas copier ailleurs, ne pas partager.
# Les chemins (donnees, llama.cpp, modeles) et les ports sont fournis par ocr_env.bat.
OCR_SECRET_KEY={key}
OCR_OCR_BACKEND=surya2
# true seulement si l'application est servie en HTTPS.
OCR_COOKIE_SECURE=false
OCR_OLLAMA_URL=http://127.0.0.1:11434
"""

# Ports et adresses d'écoute : jamais dans .env. Un .env d'une installation précédente qui en contient (anciens ports)
# passerait devant les valeurs par défaut dès qu'on démarre l'application sans ocr_env.bat : on le nettoie.
STALE = ("OCR_API_HOST", "OCR_API_PORT", "OCR_FRONTEND_PORT", "OCR_FRONTEND_HOST", "OCR_LLAMA_PORT", "OCR_LLAMA_HOST")


def clean(text: str) -> tuple[str, list[str]]:
    kept, removed = [], []
    for ln in text.splitlines():
        if ln.split("=", 1)[0].strip() in STALE:
            removed.append(ln.strip())
        else:
            kept.append(ln)
    return "\n".join(kept).rstrip("\n") + "\n", removed


def main() -> int:
    if ENV.is_file():
        text, removed = clean(ENV.read_text(encoding="utf-8"))
        if removed:
            names = ", ".join(r.split("=")[0] for r in removed)
            print(f"    .env : {len(removed)} ligne(s) de ports retirée(s) ({names}).")
        has_key = any(ln.startswith("OCR_SECRET_KEY=") and len(ln.split("=", 1)[1].strip()) >= 32
                      for ln in text.splitlines())
        if has_key:
            print("    .env existant conservé (clé secrète présente).")
        else:
            print("    [ATTENTION] .env existant sans clé secrète valide : une clé est ajoutée.")
            text += f"OCR_SECRET_KEY={secrets.token_urlsafe(48)}\n"
        ENV.write_text(text, encoding="utf-8")
        return 0
    ENV.parent.mkdir(parents=True, exist_ok=True)
    ENV.write_text(TEMPLATE.format(key=secrets.token_urlsafe(48)), encoding="utf-8")
    print(f"    .env créé : {ENV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
