"""Stockage des fichiers hors de la racine web : data/{uploads,pages,crops,exports}/<user_id>/<document_id>/<nom aléatoire>.

Les chemins en base sont relatifs à `data_dir`. Toute résolution vérifie qu'on reste sous `data_dir`.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.security import random_name

KINDS = ("uploads", "pages", "crops", "exports")


class StorageError(RuntimeError):
    pass


def doc_dir(kind: str, user_id: int, document_id: int, create: bool = False) -> Path:
    assert kind in KINDS
    d = get_settings().data_dir / kind / str(user_id) / str(document_id)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def new_path(kind: str, user_id: int, document_id: int, suffix: str) -> tuple[Path, str]:
    """Retourne (chemin absolu, chemin relatif à data_dir) pour un nouveau fichier au nom aléatoire."""
    p = doc_dir(kind, user_id, document_id, create=True) / random_name(suffix)
    return p, rel(p)


def rel(p: Path) -> str:
    return p.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()


def resolve(relative: str) -> Path:
    base = get_settings().data_dir.resolve()
    p = (base / relative).resolve()
    if base != p and base not in p.parents:
        raise StorageError("Chemin hors du dossier de données")
    return p


def delete_document_files(user_id: int, document_id: int) -> int:
    """Efface réellement tous les fichiers d'un document. Retourne le nombre de fichiers supprimés."""
    n = 0
    for kind in KINDS:
        d = doc_dir(kind, user_id, document_id)
        if d.exists():
            n += sum(1 for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
        parent = d.parent
        try:
            parent.rmdir()  # supprime le dossier utilisateur s'il est vide
        except OSError:
            pass
    return n


def user_usage_bytes(user_id: int) -> int:
    total = 0
    for kind in KINDS:
        d = get_settings().data_dir / kind / str(user_id)
        if d.exists():
            for root, _, files in os.walk(d):
                total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total
