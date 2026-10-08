"""Maintenance sans outil externe : migrations de la base (avec copie de sécurité), sauvegarde et restauration.

SQLite se sauvegarde à chaud par son API de sauvegarde (copie cohérente même si l'application écrit) : ni
mysqldump, ni 7-Zip, ni service à arrêter. L'archive contient la base, les fichiers déposés et les Word produits ;
les images de travail se recalculent et n'y sont pas. Placez les sauvegardes sur un disque chiffré (BitLocker).
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
import zipfile
from contextlib import closing
from pathlib import Path

from app.core.config import BACKEND_DIR, get_settings
from app.core.db import sqlite_path

log = logging.getLogger("ocr.maintenance")
ARCHIVE_DIRS = ("uploads", "exports", "crops")  # découpes : utiles à la relecture et au Word des documents à relire


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def revisions() -> tuple[str | None, str | None]:
    """(révision de la base, révision attendue par le code)."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from app.core.db import get_engine

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    with get_engine().connect() as c:
        current = MigrationContext.configure(c).get_current_revision()
    return current, head


def snapshot_db(dest: Path) -> Path:
    """Copie cohérente de la base SQLite (API de sauvegarde : l'application peut continuer d'écrire)."""
    src = sqlite_path(get_settings().database_url)
    if src is None:
        raise RuntimeError("La sauvegarde intégrée ne gère que la base SQLite.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise FileNotFoundError(f"Base introuvable : {src}")
    _copy_db(src, dest)
    with closing(sqlite3.connect(str(dest))) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("La copie de la base est incohérente (integrity_check).")
    return dest


def _copy_db(src: Path, dest: Path) -> None:
    """Copie page à page par l'API de sauvegarde de SQLite (cohérente, WAL compris). Connexions toujours fermées :
    sous Windows, une connexion oubliée garde le fichier verrouillé."""
    with closing(sqlite3.connect(str(src), timeout=60)) as source, closing(sqlite3.connect(str(dest), timeout=60)) as target:
        source.backup(target)


def migrate(backup: bool = True) -> str:
    """Met la base au niveau du code. Une copie de la base est faite avant toute migration réelle."""
    from alembic import command

    current, head = revisions()
    if current == head:
        return f"base à jour ({head})"
    src = sqlite_path(get_settings().database_url)
    if backup and current is not None and src is not None and src.exists():
        dest = get_settings().data_dir / "backups" / f"avant-migration-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
        snapshot_db(dest)
        log.info("copie de la base avant migration : %s", dest)
    command.upgrade(_alembic_config(), "head")
    return f"base migrée de {current or 'vide'} vers {head}"


def backup(destination: Path, keep: int = 30) -> Path:
    """Archive ZIP : base (copie cohérente) + fichiers déposés + Word produits. Garde les `keep` plus récentes."""
    s = get_settings()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = destination / f".ocr-{stamp}.tmp"
    out = destination / f"ocr-{stamp}.zip"
    try:
        db = snapshot_db(work / "base.sqlite3")
        part = out.with_suffix(".part")
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.write(db, "base.sqlite3")
            files = 0
            for kind in ARCHIVE_DIRS:
                root = s.data_dir / kind
                if root.exists():
                    for f in root.rglob("*"):
                        if f.is_file():
                            z.write(f, f"{kind}/{f.relative_to(root).as_posix()}", compress_type=zipfile.ZIP_STORED)
                            files += 1
            z.writestr("sauvegarde.json", json.dumps({"date": stamp, "fichiers": files}, ensure_ascii=False))
        part.replace(out)  # l'archive n'apparaît sous son nom final qu'une fois complète
    finally:
        shutil.rmtree(work, ignore_errors=True)
    old = sorted(destination.glob("ocr-*.zip"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink(missing_ok=True)
    return out


def restore(archive: Path) -> str:
    """Restaure une archive de backup() : la base actuelle est d'abord copiée à côté. Application ARRÊTÉE."""
    s = get_settings()
    src = sqlite_path(s.database_url)
    if src is None:
        raise RuntimeError("La restauration intégrée ne gère que la base SQLite.")
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"Archive abîmée ({bad}).")
        names = z.namelist()
        if "base.sqlite3" not in names:
            raise RuntimeError("Archive invalide : base.sqlite3 absent.")
        for n in names:  # jamais d'écriture hors du dossier de données
            if n.startswith("/") or ".." in Path(n).parts:
                raise RuntimeError("Archive invalide (chemin suspect).")
        if src.exists():
            keep = s.data_dir / "backups" / f"avant-restauration-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
            snapshot_db(keep)
        tmp = src.with_name(src.name + ".restauration")
        try:
            with z.open("base.sqlite3") as fin, open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            with closing(sqlite3.connect(str(tmp))) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("La base contenue dans l'archive est abîmée.")
            # Recopie DANS la base existante par l'API de sauvegarde : journal WAL et connexions restent cohérents
            # (aucun fichier -wal à supprimer à la main).
            src.parent.mkdir(parents=True, exist_ok=True)
            _copy_db(tmp, src)
        finally:
            tmp.unlink(missing_ok=True)
        for n in names:
            if n.split("/", 1)[0] in ARCHIVE_DIRS and not n.endswith("/"):
                target = s.data_dir / n
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as fin, open(target, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
    return f"restauration terminée depuis {archive.name}"
