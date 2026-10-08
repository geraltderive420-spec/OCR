"""Commandes d'exploitation : création du premier administrateur, purge, état.

    python -m app.cli create-admin <identifiant>      (mot de passe demandé, ou variable OCR_ADMIN_PASSWORD)
    python -m app.cli purge
    python -m app.cli seed-dev                         (compte de DÉVELOPPEMENT « demo », refusé en production)
    python -m app.cli check                            (auto-diagnostic : configuration, base, moteur OCR, Ollama)
    python -m app.cli selftest                         (lit une page de test avec le VRAI moteur OCR : GPU, modèles, serveur)
    python -m app.cli migrate                          (met la base au niveau du code, copie de sécurité avant)
    python -m app.cli backup <dossier> [--garder 30]   (archive ZIP : base + fichiers, à chaud)
    python -m app.cli restore <archive.zip>            (application ARRÊTÉE ; la base actuelle est d'abord copiée)
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.core.db import init_engine, session_scope
from app.core.security import check_password_strength, hash_password
from app.models import Role, User
from app.services.retention import purge_expired


def create_admin(username: str, password: str | None = None) -> int:
    password = password or os.environ.get("OCR_ADMIN_PASSWORD") or getpass.getpass("Mot de passe : ")
    problem = check_password_strength(password)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    with session_scope() as db:
        if db.scalar(select(User).where(User.username == username.lower())):
            print("Cet identifiant existe déjà.", file=sys.stderr)
            return 1
        db.add(User(username=username.lower(), password_hash=hash_password(password), role=Role.admin.value,
                    must_change_password=True))
    print(f"Administrateur « {username.lower()} » créé (changement de mot de passe exigé à la première connexion).")
    return 0


def seed_dev() -> int:
    """Crée (ou remet à jour) le compte de développement. Refusé en production."""
    from app.core.config import get_settings
    from app.dev_seed import DEV_PASSWORD, DEV_ROLE, DEV_USERNAME

    if get_settings().cookie_secure:
        print("Refusé : OCR_COOKIE_SECURE=true indique un environnement de production.", file=sys.stderr)
        return 3
    with session_scope() as db:
        u = db.scalar(select(User).where(User.username == DEV_USERNAME))
        if u is None:
            u = User(username=DEV_USERNAME, role=DEV_ROLE)
            db.add(u)
        u.password_hash = hash_password(DEV_PASSWORD)
        u.role, u.is_active, u.must_change_password = DEV_ROLE, True, False
        u.failed_attempts, u.locked_until = 0, None
        u.token_version = (u.token_version or 0) + 1
    print(f"Compte de développement « {DEV_USERNAME} » prêt (identifiants dans backend/app/dev_seed.py).")
    return 0


def selftest() -> int:
    """Démarre le moteur OCR réel, lit une page générée et vérifie le texte : prouve que GPU, modèles et llama-server
    fonctionnent ENSEMBLE sur ce poste (le diagnostic `check` ne vérifie que la présence des fichiers)."""
    import time

    from PIL import Image, ImageDraw, ImageFont

    from app.services.ocr.html_utils import to_text
    from app.services.ocr_config import OcrConfig
    from app.worker.runner import make_engine

    cfg = OcrConfig.load()
    img = Image.new("RGB", (1240, 800), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(name, 46)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default(size=46)
    for i, line in enumerate(("REPUBLIQUE FRANCAISE", "Courrier de test numero 2026", "Objet : verification du moteur OCR")):
        draw.text((90, 120 + i * 110), line, fill="black", font=font)
    engine = make_engine(cfg)
    t0 = time.time()
    try:
        print(f"Moteur : {cfg.backend} ; démarrage du serveur d'inférence (peut prendre 1 à 3 minutes)...")
        engine.start()
        mode = getattr(getattr(engine, "server", None), "mode", "gpu")
        print(f"Serveur prêt en {time.time() - t0:.0f} s ({'processeur (repli)' if mode == 'cpu' else 'GPU'}).")
        t1 = time.time()
        page = engine.recognize(img)
        text = " ".join(to_text(b.html) for b in page.blocks).lower()
        print(f"Page lue en {time.time() - t1:.0f} s : {len(page.blocks)} bloc(s).")
    except Exception as e:  # noqa: BLE001
        print(f"KO : le moteur OCR n'a pas fonctionné ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    finally:
        engine.stop()
    if cfg.backend != "dummy" and not ("2026" in text and "courrier" in text):
        print("KO : le texte de la page de test n'a pas été reconnu.", file=sys.stderr)
        return 1
    print("OK : le moteur OCR lit correctement une page.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin")
    c.add_argument("username")
    sub.add_parser("purge")
    sub.add_parser("check")
    sub.add_parser("seed-dev")
    sub.add_parser("migrate")
    sub.add_parser("selftest")
    sub.add_parser("has-users")
    b = sub.add_parser("backup")
    b.add_argument("destination")
    b.add_argument("--garder", type=int, default=30)
    r = sub.add_parser("restore")
    r.add_argument("archive")
    args = ap.parse_args(argv)
    init_engine()
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "has-users":  # code de sortie 0 : au moins un compte existe (utilisé par l'installation)
        with session_scope() as db:
            return 0 if db.scalar(select(User.id).limit(1)) is not None else 1
    if args.cmd in ("migrate", "backup", "restore"):
        from pathlib import Path

        from app import maintenance

        try:
            if args.cmd == "migrate":
                print(maintenance.migrate(backup=True))
            elif args.cmd == "backup":
                print(f"Sauvegarde : {maintenance.backup(Path(args.destination), keep=args.garder)}")
            else:
                print(maintenance.restore(Path(args.archive)))
        except Exception as e:  # noqa: BLE001
            print(f"Échec : {e}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "create-admin":
        return create_admin(args.username)
    if args.cmd == "seed-dev":
        return seed_dev()
    if args.cmd == "check":
        from app.selfcheck import main as check_main

        return check_main()
    if args.cmd == "purge":
        with session_scope() as db:
            print(f"{purge_expired(db)} document(s) purgé(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
