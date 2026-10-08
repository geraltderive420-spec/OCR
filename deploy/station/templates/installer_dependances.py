"""Installation hors ligne des dépendances Python du colis OCR.

Exécuté PAR le python du venv (`.venv\\Scripts\\python.exe`), depuis import_station.bat. Fait ce qu'un simple
`pip install -r requirements.lock.txt` ne sait pas faire :

  - un seul paquet sans roue fait échouer tout le lot et pip ne dit pas lequel : on repasse paquet par paquet ;
  - on distingue « roue absente du colis » (copie USB incomplète) de « installation refusée » (roue corrompue) ;
  - on saute ce qui est déjà installé à la bonne version : une reprise après échec ne recommence pas 4 Go ;
  - torch / torchvision (version locale +cu126, absente de PyPI) s'installent depuis leur fichier .whl.

Aucun accès réseau : `--no-index` est posé sur chaque appel pip.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
PY = Path(sys.executable)

# Modules sans lesquels l'application est inutilisable.
REQUIRED_MODULES = ["fastapi", "uvicorn", "sqlalchemy", "alembic", "pydantic", "pydantic_settings", "docx", "cv2", "numpy",
                    "PIL", "pypdfium2", "httpx", "argon2", "jwt", "multipart", "rapidfuzz", "torch", "torchvision",
                    "transformers", "surya", "huggingface_hub", "platformdirs"]


def log(msg: str = "") -> None:
    print(f"    {msg}", flush=True)


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def index_wheels(folder: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for w in folder.glob("*.whl"):
        out.setdefault(canon(w.name.split("-")[0]), []).append(w)
    return out


def read_specs(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def pip(args: list[str], journal: Path) -> int:
    cmd = [str(PY), "-m", "pip", *args, "--no-index", "--disable-pip-version-check"]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(f"\n$ {' '.join(cmd)}\n{proc.stdout}{proc.stderr}")
    return proc.returncode


def installed() -> dict[str, str]:
    proc = subprocess.run([str(PY), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
                          capture_output=True, text=True, errors="replace", check=False)
    try:
        return {canon(p["name"]): p["version"] for p in json.loads(proc.stdout)}
    except (ValueError, KeyError):
        return {}


def install_specs(specs: list[str], wheels: Path, journal: Path, label: str) -> list[str]:
    """Installe `specs` ; lot d'abord, puis paquet par paquet. Retourne les échecs (texte lisible)."""
    if not specs:
        return []
    present = installed()
    todo = [s for s in specs if present.get(canon(s.split("==")[0])) != s.split("==", 1)[1]]
    if not todo:
        log(f"{label} : déjà installé ({len(specs)} paquets).")
        return []
    base = ["install", "--no-deps", "--find-links", str(wheels)]
    lot = journal.parent / "lot.txt"
    lot.write_text("\n".join(todo) + "\n", encoding="utf-8")
    log(f"{label} : {len(todo)} paquet(s) à installer...")
    code = pip([*base, "-r", str(lot)], journal)
    lot.unlink(missing_ok=True)
    if code == 0:
        log(f"[OK] {label} installé en une passe")
        return []
    log("[ATTENTION] le lot a échoué : reprise paquet par paquet pour isoler la cause")
    index = index_wheels(wheels)
    failures: list[str] = []
    for spec in todo:
        name = canon(spec.split("==")[0])
        if name not in index:
            failures.append(f"{spec}  [roue absente du colis]")
        elif pip([*base, spec], journal) != 0 and pip([*base, str(sorted(index[name])[-1])], journal) != 0:
            failures.append(f"{spec}  [installation refusée : roue corrompue ?]")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kit", default=str(KIT))
    ap.add_argument("--wheels", default="", help="dossier des roues (défaut : <kit>\\wheels)")
    ap.add_argument("--lock", default="", help="verrou (défaut : <kit>\\requirements.lock.txt)")
    ap.add_argument("--torch", default="", help="verrou torch (défaut : <kit>\\torch.lock.txt)")
    args = ap.parse_args()

    kit = Path(args.kit).resolve()
    wheels = Path(args.wheels).resolve() if args.wheels else kit / "wheels"
    lock = Path(args.lock).resolve() if args.lock else kit / "requirements.lock.txt"
    torch_lock = Path(args.torch).resolve() if args.torch else kit / "torch.lock.txt"
    logs = kit / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    journal = logs / "pip.log"
    journal.write_text(f"Installation hors ligne - {PY}\n", encoding="utf-8")

    if not wheels.is_dir() or not any(wheels.glob("*.whl")):
        log(f"[ERREUR] aucune roue dans {wheels} : le dossier wheels\\ n'a pas été copié (ou partiellement).")
        return 1
    if not lock.is_file():
        log(f"[ERREUR] verrou introuvable : {lock}")
        return 1
    log(f"interpréteur : {PY}")
    log(f"roues        : {len(list(wheels.glob('*.whl')))} fichiers dans {wheels.name}\\")

    pip(["install", "--upgrade", "--find-links", str(wheels), "pip", "setuptools", "wheel"], journal)  # non bloquant
    failures = install_specs(read_specs(lock), wheels, journal, "dépendances")
    failures += install_specs(read_specs(torch_lock), wheels, journal, "torch CUDA 12.6 (volumineux, patience)")

    missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    log()
    for f in failures:
        log(f"[{'ECHEC' if missing else 'ATTENTION'}] {f}")
    for m in missing:
        log(f"[ERREUR] module requis introuvable après installation : {m}")
    if missing:
        log()
        log("Installation incomplète. Causes les plus fréquentes :")
        log("  - wheels\\ copié partiellement : lancez verifier_colis.bat")
        log("  - version de Python différente de 3.13 x64 (roues cp313)")
        log(f"  - détail complet : {journal}")
        return 1

    # Vérification de torch + GPU : information, jamais bloquant (le repli processeur existe).
    probe = subprocess.run([str(PY), "-c",
                            "import torch;print(torch.__version__, 'CUDA' if torch.cuda.is_available() else 'processeur seul',"
                            "torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"],
                           capture_output=True, text=True, errors="replace", check=False)
    log(f"torch : {probe.stdout.strip() or 'import impossible (' + probe.stderr.strip()[-120:] + ')'}")
    if probe.returncode != 0:
        log("[ATTENTION] torch ne se charge pas : runtime Visual C++ manquant ? (installateur dans installers\\)")
        return 1
    log("[OK] toutes les dépendances requises sont installées.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
