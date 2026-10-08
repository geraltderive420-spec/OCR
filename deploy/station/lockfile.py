"""Verrou des dépendances de l'application : fermeture transitive de backend/requirements.txt.

Le venv de développement hérite des paquets de conda (--system-site-packages) : un `pip freeze` y mêlerait des
centaines de paquets sans rapport. On part donc des seules exigences de l'application et on suit leurs dépendances
dans l'environnement installé (marqueurs d'environnement évalués pour Windows / Python 3.13), avec les versions
exactes réellement testées.

    python lockfile.py [--requirements ...] [--exclude torch,...]   -> lignes « nom==version » sur la sortie standard
"""
from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

ENV = {**default_environment(), "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt",
       "platform_machine": "AMD64", "extra": ""}


def read_requirements(path: Path) -> list[Requirement]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.append(Requirement(line))
    return out


def closure(roots: list[Requirement]) -> tuple[dict[str, str], list[str]]:
    """({nom canonique: version installée}, [problèmes])."""
    found: dict[str, str] = {}
    problems: list[str] = []
    todo = list(roots)
    while todo:
        req = todo.pop()
        name = canonicalize_name(req.name)
        if name in found:
            continue
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            problems.append(f"{req.name} : non installé dans l'environnement source")
            continue
        found[name] = dist.version
        for dep in dist.requires or []:
            try:
                r = Requirement(dep)
            except InvalidRequirement:
                continue
            if r.marker is None or r.marker.evaluate({**ENV, "extra": ""}):
                todo.append(r)
    return found, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requirements", default=str(Path(__file__).resolve().parents[2] / "backend" / "requirements.txt"))
    ap.add_argument("--exclude", default="", help="paquets à ne pas verrouiller (virgules)")
    ap.add_argument("--show-mismatch", action="store_true")
    args = ap.parse_args()
    roots = read_requirements(Path(args.requirements))
    found, problems = closure(roots)
    for r in roots:
        v = found.get(canonicalize_name(r.name))
        if v and r.specifier and not r.specifier.contains(v, prereleases=True):
            problems.append(f"{r.name} : installé {v}, requirements.txt demande {r.specifier}")
    skip = {canonicalize_name(x) for x in args.exclude.split(",") if x}
    for name in sorted(found):
        if name in skip:
            continue
        print(f"{name}=={found[name]}")
    for p in problems:
        print(f"# PROBLEME {p}", file=sys.stderr)
    return 1 if any("non installé" in p for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
