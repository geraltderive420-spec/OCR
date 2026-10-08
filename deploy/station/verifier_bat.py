"""Analyseur statique des scripts .bat du kit de deploiement.

Deux pieges de cmd.exe ont deja casse un import en conditions reelles ; ils ne
sont visibles ni a la lecture ni a la compilation, seulement a l'execution de
la branche concernee. D'ou ce controle automatique :

  1. Parenthese non echappee dans un `echo` situe DANS un bloc `( ... )`.
     Le `)` ferme le bloc en avance et le reste de la ligne devient une
     commande invalide -> ". n'etait pas attendu".

  2. `for /f ... in ('"chemin" args')` : quand la commande commence par un
     guillemet, cmd /c depouille les quotes de travers et la boucle ne
     renvoie rien. La variable reste vide, silencieusement.

  3. `%VAR%` lu a l'interieur du bloc `( ... )` ou il vient d'etre pose :
     l'expansion a lieu au moment de l'analyse du bloc, donc la valeur lue
     est celle d'AVANT le bloc.

Usage :  python tools/verifier_bat.py [fichiers.bat ...]
Sortie non nulle si au moins un probleme est trouve.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Une parenthese est consideree protegee si precedee de ^ ou dans "..." .
ECHO_RE = re.compile(r"^\s*echo\b(.*)$", re.IGNORECASE)
FORF_RE = re.compile(r"""for\s+/f\b[^(]*\(\s*'(.*?)'\s*\)""", re.IGNORECASE | re.DOTALL)
SET_RE = re.compile(r"""^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=""", re.IGNORECASE)

problems: list[tuple[str, int, str, str]] = []


def strip_quoted(text: str) -> str:
    """Retire le contenu entre guillemets : cmd n'y interprete pas ( et )."""
    return re.sub(r'"[^"]*"', '""', text)


def unescaped_parens(text: str) -> bool:
    text = strip_quoted(text)
    for i, ch in enumerate(text):
        if ch in "()" and (i == 0 or text[i - 1] != "^"):
            return True
    return False


def depth_delta(line: str) -> int:
    """Variation de profondeur de bloc apportee par cette ligne."""
    body = strip_quoted(line)
    # Un echo n'ouvre ni ne ferme de bloc, sauf la parenthese parasite qu'on
    # signale par ailleurs : on ne compte que les lignes de structure.
    delta = 0
    for i, ch in enumerate(body):
        if i and body[i - 1] == "^":
            continue
        if ch == "(":
            delta += 1
        elif ch == ")":
            delta -= 1
    return delta


def check(path: Path) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    depth = 0
    assigned_in_block: dict[int, set[str]] = {}

    for no, raw in enumerate(lines, 1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("rem ") or stripped.startswith("::"):
            continue

        # -- 1. parentheses non echappees dans un echo a l'interieur d'un bloc
        echo = ECHO_RE.match(stripped)
        if echo and depth > 0 and unescaped_parens(echo.group(1)):
            problems.append((
                str(path.relative_to(ROOT)), no,
                "parenthese non echappee dans un echo a l'interieur d'un bloc "
                "( ... ) : elle ferme le bloc en avance",
                stripped,
            ))

        # -- 2. for /f dont la commande commence par un guillemet
        for match in FORF_RE.finditer(stripped):
            if match.group(1).lstrip().startswith('"'):
                problems.append((
                    str(path.relative_to(ROOT)), no,
                    "for /f dont la commande commence par un guillemet : "
                    "cmd /c depouille mal les quotes, la boucle renvoie vide",
                    stripped,
                ))

        # -- 3. variable posee puis relue dans le meme bloc
        assign = SET_RE.match(stripped)
        if assign and depth > 0:
            assigned_in_block.setdefault(depth, set()).add(assign.group(1).upper())
        if depth > 0:
            for lvl in range(1, depth + 1):
                for var in assigned_in_block.get(lvl, ()):  # noqa: B007
                    if not echo and f"%{var}%".upper() in stripped.upper():
                        if not SET_RE.match(stripped) or f"%{var}%".upper() in stripped.upper()[stripped.upper().find("="):]:
                            problems.append((
                                str(path.relative_to(ROOT)), no,
                                f"%{var}% est lu dans le bloc ou il vient d'etre "
                                "pose : l'expansion a lieu a l'analyse du bloc "
                                "(utiliser EnableDelayedExpansion et !%s!)" % var,
                                stripped,
                            ))
                            break

        new_depth = depth + depth_delta(line) if not echo else depth + 0
        if new_depth < 0:
            new_depth = 0
        if new_depth == 0:
            assigned_in_block.clear()
        depth = new_depth


def main() -> int:
    targets = [Path(a).resolve() for a in sys.argv[1:]]
    if not targets:
        targets = sorted((ROOT / "templates").glob("*.bat"))
        targets.append(ROOT.parents[1] / "export_station.bat") if (ROOT.parents[1] / "export_station.bat").is_file() else None

    for path in targets:
        if path.is_file():
            check(path)

    if not problems:
        print(f"[OK] {len(targets)} script(s) analyse(s), aucun probleme detecte.")
        return 0

    print(f"{len(problems)} probleme(s) detecte(s) :\n")
    for file, no, why, text in problems:
        print(f"  {file}:{no}")
        print(f"    {why}")
        print(f"    > {text}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
