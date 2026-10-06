"""Commandes d'exploitation : création du premier administrateur, purge, état.

    python -m app.cli create-admin <identifiant>      (mot de passe demandé, ou variable OCR_ADMIN_PASSWORD)
    python -m app.cli purge
    python -m app.cli check                            (auto-diagnostic : configuration, base, moteur OCR, Ollama)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin")
    c.add_argument("username")
    sub.add_parser("purge")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    init_engine()
    if args.cmd == "create-admin":
        return create_admin(args.username)
    if args.cmd == "check":
        from app.selfcheck import main as check_main

        return check_main()
    if args.cmd == "purge":
        with session_scope() as db:
            print(f"{purge_expired(db)} document(s) purgé(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
