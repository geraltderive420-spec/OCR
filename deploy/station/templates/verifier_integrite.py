"""Controle d'integrite du colis apres copie (cle USB, disque, reseau).

Une copie Windows interrompue ne previent pas toujours : un wheel tronque a
l'air normal dans l'explorateur et ne se trahit qu'au moment de
l'installation, plusieurs minutes plus tard, avec un message illisible. On
verifie donc le colis AVANT d'installer quoi que ce soit.

Le fichier `empreintes.sha256` est produit par export.bat sur la machine
source. Format d'une ligne :

    <sha256 en hexadecimal> <taille en octets> <chemin relatif, separe par />

Les lignes commencant par `#` sont des commentaires.

Usage :
    python verifier_integrite.py            controle complet (SHA-256)
    python verifier_integrite.py --rapide   presence et taille seulement
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
MANIFESTE = "empreintes.sha256"


def log(msg: str = "") -> None:
    print(f"    {msg}", flush=True)


def human(size: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Go"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lire_manifeste(path: Path) -> list[tuple[str, int, str]]:
    entrees = []
    for numero, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        ligne = raw.strip()
        if not ligne or ligne.startswith("#"):
            continue
        # Le chemin est le dernier champ et peut contenir des espaces : on ne
        # decoupe donc que sur les deux premieres separations.
        parties = ligne.split(" ", 2)
        if len(parties) != 3:
            log(f"[ATTENTION] ligne {numero} illisible, ignoree : {ligne[:60]}")
            continue
        empreinte, taille, rel = parties
        try:
            entrees.append((empreinte, int(taille), rel))
        except ValueError:
            log(f"[ATTENTION] taille illisible ligne {numero}, ignoree")
    return entrees


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", default=str(KIT), help="racine du colis a controler")
    parser.add_argument(
        "--rapide",
        action="store_true",
        help="ne verifie que la presence et la taille (quelques secondes)",
    )
    args = parser.parse_args()

    kit = Path(args.kit).resolve()
    manifeste = kit / MANIFESTE
    if not manifeste.is_file():
        # Un colis sans manifeste reste installable : on ne bloque pas, mais
        # on le dit clairement, car plus rien ne detecte alors une copie
        # partielle avant l'installation.
        log(f"[ATTENTION] {MANIFESTE} absent : integrite non verifiable.")
        return 0

    entrees = lire_manifeste(manifeste)
    if not entrees:
        log(f"[ERREUR] {MANIFESTE} ne contient aucune entree exploitable.")
        return 1

    attendu = sum(taille for _, taille, _ in entrees)
    mode = "presence et taille" if args.rapide else "SHA-256 complet"
    log(f"{len(entrees)} fichiers, {human(attendu)} attendus - controle : {mode}")

    absents: list[str] = []
    tailles: list[str] = []
    corrompus: list[str] = []
    traites = 0
    octets = 0

    for empreinte, taille, rel in entrees:
        cible = kit / rel
        if not cible.is_file():
            absents.append(rel)
            continue
        reelle = cible.stat().st_size
        if reelle != taille:
            tailles.append(f"{rel} ({human(reelle)} au lieu de {human(taille)})")
            continue
        if not args.rapide and sha256(cible) != empreinte:
            corrompus.append(rel)
            continue
        traites += 1
        octets += taille
        # Un colis complet depasse les 5000 fichiers : sans reperes, l'ecran
        # reste fige plusieurs minutes et l'operateur croit a un blocage.
        if traites % 500 == 0:
            log(f"  ... {traites}/{len(entrees)} fichiers ({human(octets)})")

    log()
    for rel in absents[:15]:
        log(f"[MANQUANT]  {rel}")
    if len(absents) > 15:
        log(f"[MANQUANT]  ... et {len(absents) - 15} autre(s)")
    for msg in tailles[:15]:
        log(f"[TRONQUE]   {msg}")
    if len(tailles) > 15:
        log(f"[TRONQUE]   ... et {len(tailles) - 15} autre(s)")
    for rel in corrompus[:15]:
        log(f"[ALTERE]    {rel}")
    if len(corrompus) > 15:
        log(f"[ALTERE]    ... et {len(corrompus) - 15} autre(s)")

    total = len(absents) + len(tailles) + len(corrompus)
    if total:
        log()
        log(f"[ERREUR] {total} fichier(s) manquant(s) ou abime(s) sur {len(entrees)}.")
        log("         La copie du colis est incomplete : recopiez le dossier")
        log("         entier depuis la machine source, puis relancez.")
        return 1

    log(f"[OK] colis complet et intact ({len(entrees)} fichiers, {human(octets)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
