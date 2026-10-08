"""Fabrique le colis de déploiement HORS LIGNE pour la station de production (appelé par export_station.bat).

Station visée : Windows 11, RTX 3060 12 Go, aucun accès réseau, ni pendant l'installation ni ensuite.
Le colis (dossier `transfert_station/`) est autonome :

    app/            code de l'application (backend + interface compilée), sans données ni secrets
    wheels/         toutes les dépendances Python en roues binaires Windows x64 (torch CUDA cu126 compris)
    python/         installeur Python 3.13 x64 (la station n'a pas forcément Python)
    installers/     runtime Visual C++ 2015-2022 x64
    models/         modèles : Surya 2 (GGUF), Heron, PaddleOCR-VL (cache Hugging Face) et détecteur de lignes (datalab)
    llama/          llama-server (build CUDA) + DLL CUDA 12 ; llama-cpu/ : build processeur de secours (facultatif)
    *.bat, *.py     import, vérification, démarrage, sauvegarde
    empreintes.sha256   SHA-256 de chaque fichier : détecte une copie USB incomplète avant l'installation

Aucune base à installer : SQLite est créée au premier démarrage. Ollama et le pilote NVIDIA ne sont pas fournis (déjà
présents sur la station).

Usage : python export_kit.py [--skip-models] [--skip-wheels] [--skip-hash] [--llama-dir D] [--llama-cpu-dir D]
                             [--out DOSSIER] [--maj]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TEMPLATES = HERE / "templates"
STAGING = ROOT / ".export_cache"
sys.path.insert(0, str(HERE))
from lockfile import closure, read_requirements  # noqa: E402

PY_TAG, PY_DOTTED = "313", "3.13"
PY_PATCHES = ["3.13.13", "3.13.12", "3.13.11", "3.13.10", "3.13.9", "3.13.8", "3.13.7", "3.13.6", "3.13.5"]
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH_PACKAGES = ("torch", "torchvision")  # versions locales « +cu126 » : absentes de PyPI
VCREDIST_URLS = ["https://aka.ms/vs/17/release/vc_redist.x64.exe", "https://aka.ms/vs/16/release/vc_redist.x64.exe"]

# Modèles : (dépôt Hugging Face, fichiers ; None = tout le dépôt). Doit refléter gpu.py, second_reader.py, heron.py.
HF_MODELS = [
    ("datalab-to/surya-ocr-2-gguf", ["surya-2.gguf", "surya-2-mmproj.gguf"], True),
    ("docling-project/docling-layout-heron", None, False),
    ("PaddlePaddle/PaddleOCR-VL-1.5-GGUF", ["PaddleOCR-VL-1.5.gguf", "PaddleOCR-VL-1.5-mmproj.gguf"], False),
]  # 3e champ : indispensable (sans lui, le colis est refusé) ; les autres sont des améliorations désactivables
DATALAB_DETECTOR = "text_detection"  # détecteur de lignes de Surya (surya.settings.DETECTOR_MODEL_CHECKPOINT)

# Modules importés par le code -> distribution PyPI, quand les noms diffèrent.
IMPORT_TO_DIST = {"cv2": "opencv-python-headless", "docx": "python-docx", "jwt": "PyJWT", "PIL": "pillow",
                  "multipart": "python-multipart", "argon2": "argon2-cffi", "rapidfuzz": "RapidFuzz",
                  "surya": "surya-ocr", "sqlalchemy": "SQLAlchemy", "pydantic_settings": "pydantic-settings",
                  "huggingface_hub": "huggingface-hub", "pypdfium2": "pypdfium2", "platformdirs": "platformdirs"}
APP_EXCLUDE_DIRS = {".venv", "node_modules", "__pycache__", ".git", ".pytest_tmp", ".pytest_cache", "data", "tests",
                    ".claude"}
APP_EXCLUDE_FILES = {".env"}
APP_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log"}


class Fail(RuntimeError):
    """Erreur bloquante : l'export s'arrête et affiche FAILED."""


_step = 0


def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n{'=' * 74}\n[{_step}] {title}\n{'=' * 74}", flush=True)


def info(msg: str) -> None:
    print(f"    {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"    [ATTENTION] {msg}", flush=True)


def human(size: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Go"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], *, check: bool = True, quiet: bool = False, env: dict | None = None) -> int:
    if not quiet:
        info("$ " + " ".join(str(c) for c in cmd[:6]) + (" ..." if len(cmd) > 6 else ""))
    code = subprocess.run(cmd, check=False, env=env).returncode
    if check and code != 0:
        raise Fail(f"commande en échec (code {code}) : {' '.join(str(c) for c in cmd[:4])} ...")
    return code


def link_or_copy(src: Path, dst: Path) -> None:
    """Lien physique (même volume NTFS : pas de doublon sur le disque), sinon copie."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except (OSError, NotImplementedError):
        shutil.copy2(src, dst)


def mirror(src: Path, dst: Path) -> None:
    for f in src.rglob("*"):
        if f.is_file():
            link_or_copy(f, dst / f.relative_to(src))


def download_file(urls: list[str], target: Path, min_size: int, label: str, magic: bytes | None = None) -> bool:
    """Télécharge vers `target` (fichier .part renommé à la fin) ; essaie chaque adresse ; contrôle taille et type."""
    if target.is_file() and target.stat().st_size >= min_size:
        info(f"déjà présent : {target.name} ({human(target.stat().st_size)})")
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        part = target.with_suffix(target.suffix + ".part")
        try:
            info(f"téléchargement {url}")
            with urllib.request.urlopen(url, timeout=120) as resp, part.open("wb") as fh:
                shutil.copyfileobj(resp, fh, length=1 << 20)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            info(f"indisponible ({exc})")
            part.unlink(missing_ok=True)
            continue
        ok = part.stat().st_size >= min_size
        if ok and magic:
            with part.open("rb") as fh:
                ok = fh.read(len(magic)) == magic
        if not ok:  # une page d'erreur HTML en « 200 » ne fait que quelques Ko
            warn(f"{label} : fichier reçu invalide ({human(part.stat().st_size)}), rejeté")
            part.unlink(missing_ok=True)
            continue
        part.replace(target)
        info(f"OK : {target.name} ({human(target.stat().st_size)})")
        return True
    return False


# --------------------------------------------------------------------------
# 1. Dépendances Python
# --------------------------------------------------------------------------
def scan_imports(backend_app: Path) -> set[str]:
    tops: set[str] = set()
    for py in backend_app.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"), str(py))
        except SyntaxError as exc:
            warn(f"non analysable, ignoré : {py.relative_to(ROOT)} ({exc})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops.add(node.module.split(".")[0])
    return {t for t in tops if t not in sys.stdlib_module_names and t != "app"}


def build_lock(requirements: Path) -> tuple[list[str], dict[str, str]]:
    """(lignes du verrou sans torch, {torch / torchvision: version locale})."""
    found, problems = closure(read_requirements(requirements))
    fatal = [p for p in problems if "non installé" in p]
    for p in problems:
        warn(p)
    if fatal:
        raise Fail("dépendances absentes de l'environnement source : le colis serait incomplet.")
    reqs = {re.sub(r"[-_.]+", "-", name).lower(): ver for name, ver in found.items()}
    # Couverture : chaque module importé par le code doit être dans le verrou.
    have = set(reqs)
    missing = []
    for mod in sorted(scan_imports(ROOT / "backend" / "app")):
        dist = re.sub(r"[-_.]+", "-", IMPORT_TO_DIST.get(mod, mod)).lower()
        if dist not in have and re.sub(r"[-_.]+", "-", mod).lower() not in have:
            missing.append(mod)
    if missing:
        raise Fail("importé par le code mais absent des dépendances verrouillées : " + ", ".join(missing)
                   + "\n  -> ajoutez-le à backend/requirements.txt.")
    info(f"{len(reqs)} paquets verrouillés, tous les modules importés par le code sont couverts.")
    torch = {n: reqs.pop(n) for n in TORCH_PACKAGES if n in reqs}
    if "torch" not in torch:
        raise Fail("torch n'est pas installé dans l'environnement source.")
    if "+cu126" not in torch["torch"]:
        warn(f"torch source = {torch['torch']} : le colis embarquera quand même la variante CUDA 12.6 de la même version.")
    return [f"{n}=={v}" for n, v in sorted(reqs.items())], torch


def pip_download(python: Path, args: list[str], dest: Path) -> int:
    cmd = [str(python), "-m", "pip", "download", "--no-cache-dir", "--only-binary=:all:", "--no-deps", "--platform", "win_amd64",
           "--python-version", PY_DOTTED, "--implementation", "cp", "--abi", f"cp{PY_TAG}", "-d", str(dest)]
    return run(cmd + args, check=False, quiet=True)


def pip_build_wheel(python: Path, spec: str, dest: Path) -> int:
    """Paquet publié sans roue : construit ici (machine connectée, avec chaîne de build) ; la station ne reçoit qu'une roue."""
    return run([str(python), "-m", "pip", "wheel", "--no-cache-dir", spec, "--no-deps", "-w", str(dest)],
               check=False, quiet=True)


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def wheel_info(path: Path) -> tuple[str, str] | None:
    """(nom canonique, version avec suffixe local) d'une roue, ou None si le nom est illisible."""
    parts = path.name[:-4].split("-")
    return (canon(parts[0]), parts[1].replace("_", "+", 1) if "_" in parts[1] else parts[1]) if len(parts) >= 5 else None


def wheel_compatible(path: Path) -> bool:
    tags = path.name[:-4].split("-")[-3:]
    py, abi, plat = tags
    plat_ok = plat == "any" or "win_amd64" in plat
    py_ok = any(t.startswith("py3") or t == f"cp{PY_TAG}" or (t.startswith("cp3") and abi in ("abi3", "none"))
                for t in py.split("."))  # « py2.py3 » : roue universelle
    return plat_ok and py_ok


def wheel_versions(dest: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for w in dest.glob("*.whl"):
        i = wheel_info(w)
        if i and wheel_compatible(w):
            out.setdefault(i[0], set()).add(i[1].replace("_", "+"))
    return out


def fetch_wheels(python: Path, specs: list[str], torch: dict[str, str], dest: Path, skip: bool) -> None:
    """Garantit une roue pour CHAQUE ligne du verrou (version exacte) et pour torch / torchvision en CUDA 12.6."""
    dest.mkdir(parents=True, exist_ok=True)

    def missing() -> list[str]:
        have = wheel_versions(dest)
        out = []
        for s in specs:
            n, v = s.split("==")
            if v not in have.get(canon(n), set()):
                out.append(s)
        for n, v in torch.items():
            base = v.split("+")[0]
            if f"{base}+cu126" not in have.get(n, set()):
                out.append(f"{n}=={base}+cu126")
        return out

    todo = missing()
    if skip and not todo:
        info(f"cache réutilisé : {len(list(dest.glob('*.whl')))} roues, verrou couvert.")
        return
    if todo:
        info(f"{len(todo)} paquet(s) à récupérer sur {len(specs) + len(torch)}.")
    plain = [s for s in todo if canon(s.split("==")[0]) not in TORCH_PACKAGES]
    if plain:
        lot = STAGING / "lot.txt"
        lot.write_text("\n".join(plain) + "\n", encoding="utf-8")
        if pip_download(python, ["-r", str(lot)], dest) != 0:
            warn("le lot a échoué : reprise paquet par paquet pour isoler la cause")
            for spec in plain:
                if pip_download(python, [spec], dest) == 0:
                    continue
                info(f"pas de roue publiée pour {spec} : construction depuis la source")
                pip_build_wheel(python, spec, dest)
    for spec in [s for s in todo if canon(s.split("==")[0]) in TORCH_PACKAGES]:
        info(f"{spec} depuis {TORCH_INDEX}")
        if pip_download(python, [spec, "--index-url", TORCH_INDEX], dest) != 0:
            warn(f"{spec} non téléchargé (réseau ? version retirée de l'index ?)")
    still = missing()
    if still:
        raise Fail("roue introuvable pour : " + ", ".join(still[:8]) + (f" (+{len(still) - 8})" if len(still) > 8 else "")
                   + "\n  Le colis serait inutilisable hors ligne. Vérifiez la connexion de cette machine.")
    info(f"{len(list(dest.glob('*.whl')))} roues, {human(dir_size(dest))} ; verrou couvert.")


# --------------------------------------------------------------------------
# 2. Installeurs
# --------------------------------------------------------------------------
def fetch_python_installer(dest: Path) -> Path:
    for v in PY_PATCHES:
        name = f"python-{v}-amd64.exe"
        if download_file([f"https://www.python.org/ftp/python/{v}/{name}"], dest / name, 20_000_000,
                         "installeur Python", b"MZ"):
            return dest / name
    raise Fail(f"aucun installeur Python {PY_DOTTED}.x trouvé sur python.org (connexion de cette machine ?)")


def fetch_vcredist(dest: Path) -> Path | None:
    if download_file(VCREDIST_URLS, dest / "vc_redist.x64.exe", 5_000_000, "Visual C++", b"MZ"):
        return dest / "vc_redist.x64.exe"
    warn("runtime Visual C++ non téléchargé : le colis partira sans (Windows 11 l'a presque toujours).")
    return None


# --------------------------------------------------------------------------
# 3. Modèles
# --------------------------------------------------------------------------
def hf_source_cache() -> Path:
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(var):
            return Path(os.environ[var])
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def copy_hf_from_cache(src_hub: Path, dst_hub: Path, repo: str, files: list[str] | None) -> bool:
    """Recopie (fichiers réels, pas de liens symboliques : le colis passe par une clé USB) un dépôt du cache local."""
    folder = f"models--{repo.replace('/', '--')}"
    src = src_hub / folder
    ref = src / "refs" / "main"
    if not ref.is_file():
        return False
    rev = ref.read_text(encoding="utf-8").strip()
    snap = src / "snapshots" / rev
    if not snap.is_dir():
        return False
    wanted = [snap / f for f in files] if files else [p for p in snap.rglob("*") if p.is_file() or p.is_symlink()]
    if any(not p.exists() for p in wanted):
        return False
    dst = dst_hub / folder
    for p in wanted:
        rel = p.relative_to(snap)
        out = dst / "snapshots" / rev / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists() or out.stat().st_size != p.stat().st_size:
            shutil.copy2(p, out)  # suit les liens : copie du contenu
    (dst / "refs").mkdir(parents=True, exist_ok=True)
    (dst / "refs" / "main").write_text(rev, encoding="utf-8")
    return True


def download_hf(dst_hub: Path, repo: str, files: list[str] | None) -> None:
    from huggingface_hub import file_download, hf_hub_download, snapshot_download

    file_download.are_symlinks_supported = lambda cache_dir=None: False  # fichiers réels (voir copy_hf_from_cache)
    os.environ.pop("HF_HUB_OFFLINE", None)
    if files:
        for f in files:
            hf_hub_download(repo, f, cache_dir=str(dst_hub))
    else:
        snapshot_download(repo, cache_dir=str(dst_hub))


def verify_hf(dst_hub: Path, repo: str, files: list[str] | None) -> bool:
    from huggingface_hub import hf_hub_download, snapshot_download

    try:
        if files:
            for f in files:
                p = Path(hf_hub_download(repo, f, cache_dir=str(dst_hub), local_files_only=True))
                if p.stat().st_size < 1024:
                    return False
        else:
            snapshot_download(repo, cache_dir=str(dst_hub), local_files_only=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def datalab_source() -> Path:
    if os.environ.get("MODEL_CACHE_DIR"):
        return Path(os.environ["MODEL_CACHE_DIR"])
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("datalab")) / "models"


def fetch_models(stage: Path, hf_cache: Path | None, with_recognition: bool) -> dict:
    """Modèles dans `stage` : hf/hub (Hugging Face) et datalab/ (détecteur de lignes). Réutilise le cache local d'abord."""
    hub = stage / "hf" / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    src_hub = hf_cache or hf_source_cache()
    report: dict[str, str] = {}
    for repo, files, required in HF_MODELS:
        if verify_hf(hub, repo, files):
            info(f"déjà dans le colis : {repo}")
            report[repo] = "ok"
            continue
        ok = copy_hf_from_cache(src_hub, hub, repo, files)
        if ok:
            info(f"copié depuis le cache local : {repo}")
        else:
            info(f"absent du cache local, téléchargement : {repo}")
            try:
                download_hf(hub, repo, files)
            except Exception as exc:  # noqa: BLE001
                msg = f"{repo} : téléchargement impossible ({type(exc).__name__}: {exc})"
                if required:
                    raise Fail(msg) from exc
                warn(msg + " -> cette amélioration sera absente du colis")
        good = verify_hf(hub, repo, files)
        report[repo] = "ok" if good else "absent"
        if not good and required:
            raise Fail(f"modèle indispensable manquant ou tronqué : {repo}")
    # Détecteur de lignes de Surya (poids « datalab »).
    out = stage / "datalab"
    src = datalab_source()
    wanted = [DATALAB_DETECTOR] + (["text_recognition", "layout"] if with_recognition else [])
    for name in wanted:
        if (out / name).is_dir() and any((out / name).rglob("*.safetensors")):
            info(f"déjà dans le colis : datalab/{name}")
            continue
        if (src / name).is_dir():
            shutil.copytree(src / name, out / name, dirs_exist_ok=True)
            info(f"copié depuis {src / name}")
        elif name == DATALAB_DETECTOR:
            info("détecteur de lignes absent du cache local : téléchargement par Surya")
            env = {**os.environ, "MODEL_CACHE_DIR": str(out), "TORCH_DEVICE": "cpu"}
            code = run([sys.executable, "-c",
                        "from surya.common.s3 import download_directory;"
                        "from surya.settings import settings as s;"
                        "import os;rel=s.DETECTOR_MODEL_CHECKPOINT.replace('s3://','');"
                        "p=os.path.join(os.environ['MODEL_CACHE_DIR'],rel);os.makedirs(p,exist_ok=True);"
                        "download_directory(rel,p)"], check=False, env=env)
            if code != 0:
                warn("détecteur de lignes non téléchargé : la mise en page ne sera pas recalée sur la station.")
    report["datalab/text_detection"] = "ok" if any((out / DATALAB_DETECTOR).rglob("*.safetensors")) else "absent"
    info(f"modèles : {human(dir_size(stage))}")
    return report


# --------------------------------------------------------------------------
# 4. llama.cpp
# --------------------------------------------------------------------------
CUDA_RUNTIME = ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll")


def find_cuda_bin() -> Path | None:
    cands = []
    if os.environ.get("CUDA_PATH"):
        cands.append(Path(os.environ["CUDA_PATH"]) / "bin")
    base = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if base.is_dir():
        cands += [d / "bin" for d in sorted(base.glob("v12.*"), reverse=True)]
    return next((c for c in cands if (c / CUDA_RUNTIME[0]).is_file()), None)


def cuda_archs(dll: Path) -> tuple[set[str], set[str]]:
    """({code natif : sm_61, sm_86...}, {PTX : 61, 86...}) de ggml-cuda.dll, lus avec cuobjdump.
    Le PTX est recompilé par le pilote pour une carte plus récente (lent au premier lancement, puis en cache)."""
    # L'outil doit être aussi récent que le compilateur du binaire : celui du CUDA 12 d'abord (le PATH peut mettre un
    # CUDA 11 en premier, dont cuobjdump ne lit pas les binaires 12.x et ne répond rien).
    bin12 = find_cuda_bin()
    tool = next((p for p in ([str(bin12 / "cuobjdump.exe")] if bin12 else []) + [shutil.which("cuobjdump")]
                 if p and Path(p).is_file()), None)
    if not tool:
        return set(), set()
    found = []
    for flag, pattern in (("--list-elf", r"[.](sm_[0-9]+)[.]cubin"), ("--list-ptx", r"[.]sm_([0-9]+)[.]ptx")):
        res = subprocess.run([tool, flag, str(dll)], capture_output=True, text=True, errors="replace", check=False)
        found.append(set(re.findall(pattern, res.stdout)))
    return found[0], found[1]


def describe_archs(elf: set[str], ptx: set[str]) -> str:
    return "natif " + (", ".join(sorted(elf)) or "aucun") + " ; PTX " + (", ".join(sorted(ptx)) or "aucun")


def fetch_llama(llama_dir: Path | None, dest: Path, label: str, needs_cuda_runtime: bool) -> dict | None:
    """Copie llama-server.exe et ses DLL (pas les dizaines d'autres outils du build) ; ajoute les DLL CUDA 12."""
    exe = None
    if llama_dir:
        for sub in ("", "build/bin/Release", "build/bin", "bin/Release", "bin", "Release"):
            c = llama_dir / sub / "llama-server.exe" if sub else llama_dir / "llama-server.exe"
            if c.is_file():
                exe = c
                break
    if exe is None:
        return None
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(exe, dest / exe.name)
    for dll in exe.parent.glob("*.dll"):
        shutil.copy2(dll, dest / dll.name)
    meta = {"source": str(exe.parent), "cuda": False, "archs": None, "version": None}
    cuda = dest / "ggml-cuda.dll"
    if cuda.is_file():
        meta["cuda"] = True
        elf, ptx = cuda_archs(cuda)
        meta["archs"] = describe_archs(elf, ptx)
        meta["sm86"] = "sm_86" in elf
        if needs_cuda_runtime:
            bin_dir = find_cuda_bin()
            missing = []
            for name in CUDA_RUNTIME:
                src = (bin_dir / name) if bin_dir else None
                if src and src.is_file():
                    shutil.copy2(src, dest / name)
                else:
                    missing.append(name)
            if missing:
                warn(f"{label} : DLL CUDA introuvables sur cette machine ({', '.join(missing)}) : "
                     "llama-server ne démarrera sur la station que si elles y sont (PATH).")
    res = subprocess.run([str(dest / "llama-server.exe"), "--version"], capture_output=True, text=True,
                         errors="replace", check=False, timeout=60)
    text = (res.stdout + res.stderr)
    meta["version"] = next((ln.strip() for ln in text.splitlines() if ln.lower().startswith("version")), None)
    if res.returncode != 0:
        warn(f"{label} : llama-server --version échoue (code {res.returncode}) : DLL manquante ?")
    info(f"{label} : {meta['version']} ({human(dir_size(dest))}) ; architectures CUDA : {meta['archs']}")
    if meta["cuda"] and not meta.get("sm86"):
        if meta["archs"] == describe_archs(set(), set()) and shutil.which("cuobjdump") is None and find_cuda_bin() is None:
            warn("architectures CUDA non vérifiables (cuobjdump introuvable) : à contrôler avant de livrer.")
        elif meta["archs"].endswith("aucun"):
            warn("ce build CUDA n'a ni code natif sm_86 (RTX 3060) ni PTX : il ne tournera PAS sur la station. "
                 "Recompilez avec deploy" + chr(92) + "station" + chr(92) + "build-llama.bat.")
        else:
            warn("pas de code natif sm_86 : la RTX 3060 recompilera le PTX au premier lancement (plusieurs minutes, "
                 "une fois, puis cache) et tournera plus lentement. Recommandé : build-llama.bat puis --llama-dir. "
                 "Non vérifié sur la carte.")
    return meta


# --------------------------------------------------------------------------
# 5. Application et assemblage
# --------------------------------------------------------------------------
def copy_tree(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        if item.name in APP_EXCLUDE_DIRS or item.name in APP_EXCLUDE_FILES:
            continue
        if item.is_dir():
            copy_tree(item, dst / item.name)
        elif item.suffix.lower() not in APP_EXCLUDE_SUFFIXES:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst / item.name)


def build_frontend() -> bool:
    dist = ROOT / "frontend" / "dist" / "index.html"
    src = ROOT / "frontend" / "src"
    stale = (not dist.is_file()) or dist.stat().st_mtime < max((p.stat().st_mtime for p in src.rglob("*") if p.is_file()),
                                                               default=0)
    if not stale:
        info("interface compilée à jour.")
        return True
    npm = shutil.which("npm")
    if not npm:
        warn("npm introuvable : l'interface ne sera pas recompilée (dist existant, ou absent).")
        return dist.is_file()
    info("sources de l'interface plus récentes que dist/ : recompilation")
    fe = ROOT / "frontend"
    if not (fe / "node_modules").is_dir():
        subprocess.run([npm, "ci"], cwd=fe, check=False, shell=True)
    code = subprocess.run([npm, "run", "build"], cwd=fe, check=False, shell=True).returncode
    if code != 0 or not dist.is_file():
        raise Fail("la compilation de l'interface a échoué.")
    return True


def copy_application(app: Path) -> bool:
    app.mkdir(parents=True, exist_ok=True)
    copy_tree(ROOT / "backend", app / "backend")
    dist = ROOT / "frontend" / "dist"
    if (dist / "index.html").is_file():
        (app / "frontend").mkdir(parents=True, exist_ok=True)
        shutil.copytree(dist, app / "frontend" / "dist", dirs_exist_ok=True)
        return True
    warn("frontend/dist absent : la station n'aurait pas d'interface web.")
    return False


SCRIPTS = ("import_station.bat", "ocr_env.bat", "demarrer_ocr.bat", "creer_admin.bat", "diagnostic.bat",
           "test_ocr.bat", "sauvegarder.bat", "restaurer.bat", "demarrage_auto.bat", "ouvrir_pare_feu.bat", "verifier_colis.bat",
           "installer_dependances.py", "configurer.py", "verifier_integrite.py", "LISEZ-MOI-STATION.txt")


def copy_script(src: Path, dst: Path) -> None:
    """Copie un script ; les .bat et .txt passent en fins de ligne Windows (cmd.exe lit mal les LF seuls)."""
    data = src.read_bytes()
    if src.suffix.lower() in (".bat", ".txt"):
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    dst.write_bytes(data)


def write_checksums(out: Path) -> int:
    """Format d'une ligne : <sha256> <octets> <chemin relatif> (le chemin, dernier champ, peut contenir des espaces)."""
    target = out / "empreintes.sha256"
    target.unlink(missing_ok=True)
    lines = ["# OCR - empreintes du colis (verifier_integrite.py)", "# Format : <sha256> <octets> <chemin relatif>",
             f"# Généré le {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    n = 0
    for p in sorted(out.rglob("*")):
        if p.is_file():
            lines.append(f"{sha256(p)} {p.stat().st_size} {p.relative_to(out).as_posix()}")
            n += 1
            if n % 200 == 0:
                info(f"  ... {n} fichiers")
    lines.append(f"# {n} fichiers")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


def assemble(out: Path, *, lock: list[str], torch: dict[str, str], wheels: Path, models: Path, py_installer: Path,
             vcredist: Path | None, llama: dict | None, llama_cpu: dict | None, frontend: bool, models_report: dict,
             skip_hash: bool) -> dict:
    if out.exists():
        info(f"nettoyage de {out.name}/")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    info("application")
    has_front = copy_application(out / "app") and frontend
    info("roues Python")
    mirror(wheels, out / "wheels")
    info("modèles")
    mirror(models / "hf", out / "models" / "hf")
    if (models / "datalab").is_dir():
        mirror(models / "datalab", out / "models" / "datalab")
    for name, sub in (("llama", STAGING / "llama"), ("llama-cpu", STAGING / "llama-cpu")):
        if sub.is_dir():
            mirror(sub, out / name)
    (out / "python").mkdir()
    link_or_copy(py_installer, out / "python" / "python-setup.exe")
    if vcredist:
        link_or_copy(vcredist, out / "installers" / "vc_redist.x64.exe")
    (out / "requirements.lock.txt").write_text(
        "\n".join(lock) + "\n", encoding="utf-8")
    (out / "torch.lock.txt").write_text("\n".join(f"{n}=={v.split('+')[0]}+cu126" for n, v in torch.items()) + "\n",
                                        encoding="utf-8")
    for s in SCRIPTS:
        src = TEMPLATES / s
        if not src.is_file():
            raise Fail(f"modèle de script manquant : {src}")
        copy_script(src, out / ("LISEZ-MOI.txt" if s == "LISEZ-MOI-STATION.txt" else s))
    shutil.copy2(ROOT / "deploy" / "windows" / "make_cert.py", out / "make_cert.py")
    for d in ("logs", "data"):
        (out / d).mkdir(parents=True, exist_ok=True)
    manifest = {
        "label": "Station OCR - production hors ligne (Windows 11 / RTX 3060 12 Go)", "python": PY_DOTTED,
        "torch": {n: v.split("+")[0] + "+cu126" for n, v in torch.items()}, "packages": len(lock) + len(torch),
        "wheels": len(list((out / "wheels").glob("*.whl"))), "frontend_bundled": has_front,
        "llama_cuda": llama, "llama_cpu": llama_cpu, "models": models_report,
        "vcredist_bundled": bool(vcredist), "database": "SQLite (créée au premier démarrage)",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "source_host": os.environ.get("COMPUTERNAME", "?"),
    }
    (out / "kit_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if skip_hash:
        warn("empreintes non calculées (--skip-hash) : une copie partielle ne sera détectée qu'à l'installation.")
    else:
        step("Empreintes SHA-256 (contrôle de copie à l'arrivée)")
        info(f"{write_checksums(out)} fichiers")
    manifest["size"] = dir_size(out)
    return manifest


# --------------------------------------------------------------------------
# Mise à jour légère
# --------------------------------------------------------------------------
def build_update(python: Path, lock: list[str], torch: dict[str, str], kit: Path) -> Path:
    """Colis de mise à jour : code + interface + roues des seules dépendances changées depuis le dernier colis complet."""
    out = kit.parent / "maj_station"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    ref = kit / "requirements.lock.txt"
    old = set(ref.read_text(encoding="utf-8").split()) if ref.is_file() else None
    if old is None:
        warn("aucun colis complet de référence : le colis de mise à jour ne contiendra que le code. "
             "Si une dépendance a changé, refaites un export complet.")
    delta = [s for s in lock if old is not None and s not in old]
    if delta:
        info("dépendances modifiées : " + ", ".join(delta))
        wheels = out / "wheels"
        fetch_wheels(python, delta, {}, wheels, skip=False)
    ref_torch = kit / "torch.lock.txt"
    if ref_torch.is_file() and set(ref_torch.read_text(encoding="utf-8").split()) != {
            f"{n}=={v.split('+')[0]}+cu126" for n, v in torch.items()}:
        raise Fail("torch a changé de version : faites un export complet.")
    frontend = build_frontend()
    copy_application(out / "app")
    (out / "requirements.lock.txt").write_text("\n".join(lock) + "\n", encoding="utf-8")
    for s in ("appliquer_maj.bat", "installer_dependances.py", "verifier_integrite.py"):
        copy_script(TEMPLATES / s, out / s)
    (out / "maj_manifest.json").write_text(json.dumps({"dependances_modifiees": delta, "frontend": frontend,
                                                        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2),
                                           encoding="utf-8")
    write_checksums(out)
    return out


# --------------------------------------------------------------------------
def default_llama_dir() -> Path | None:
    env = ROOT / "backend" / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OCR_LLAMA_CPP_DIR="):
                return Path(line.split("=", 1)[1].strip().strip('"'))
    return Path(os.environ["OCR_LLAMA_CPP_DIR"]) if os.environ.get("OCR_LLAMA_CPP_DIR") else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", default="", help="interpréteur source (défaut : celui qui lance ce script)")
    ap.add_argument("--out", default=str(ROOT / "transfert_station"), help="dossier du colis")
    ap.add_argument("--skip-models", action="store_true")
    ap.add_argument("--skip-wheels", action="store_true")
    ap.add_argument("--skip-hash", action="store_true")
    ap.add_argument("--llama-dir", default="", help="build CUDA de llama.cpp (défaut : OCR_LLAMA_CPP_DIR de backend/.env)")
    ap.add_argument("--llama-cpu-dir", default="", help="build processeur de secours (facultatif)")
    ap.add_argument("--hf-cache", default="", help="cache Hugging Face source (défaut : celui de l'utilisateur)")
    ap.add_argument("--with-recognition", action="store_true", help="embarquer aussi les poids datalab de Surya 1 (3 Go)")
    ap.add_argument("--maj", action="store_true", help="colis de mise à jour léger (code + dépendances changées)")
    args = ap.parse_args()

    global STAGING
    started = time.time()
    python = Path(args.python) if args.python else Path(sys.executable)
    out = Path(args.out).resolve()
    # Le cache de préparation est voisin du colis : même volume, donc des liens physiques (pas de doublon de ~9 Go).
    STAGING = out.parent / ".export_cache"
    STAGING.mkdir(parents=True, exist_ok=True)

    step("Analyse du code et des dépendances")
    ver = subprocess.run([str(python), "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
                         capture_output=True, text=True, check=False).stdout.strip()
    info(f"interpréteur source : {python} (Python {ver})")
    if not ver.startswith(PY_DOTTED):
        warn(f"Python source {ver} : le colis vise cp{PY_TAG} ({PY_DOTTED}.x).")
    lock, torch = build_lock(ROOT / "backend" / "requirements.txt")
    info("torch : " + ", ".join(f"{n} {v}" for n, v in torch.items()))

    if args.maj:
        step("Colis de mise à jour")
        res = build_update(python, lock, torch, out)
        info(f"{res} ({human(dir_size(res))}) : à copier sur la station, puis appliquer_maj.bat")
        return 0

    step("Interface web")
    front = build_frontend()

    step("Roues Python (Windows x64, CPython 3.13)")
    wheels = STAGING / "wheels"
    fetch_wheels(python, lock, torch, wheels, args.skip_wheels)

    step("Installeur Python et runtime Visual C++")
    py_installer = fetch_python_installer(STAGING / "installers")
    vcredist = fetch_vcredist(STAGING / "installers")

    step("Modèles (Surya 2, Heron, PaddleOCR-VL, détecteur de lignes)")
    models = STAGING / "models"
    report = fetch_models(models, Path(args.hf_cache) if args.hf_cache else None, args.with_recognition) \
        if not (args.skip_models and (models / "hf").is_dir()) else {"cache": "réutilisé"}

    step("llama.cpp (llama-server)")
    llama_src = Path(args.llama_dir) if args.llama_dir else default_llama_dir()
    llama = fetch_llama(llama_src, STAGING / "llama", "build CUDA", True)
    if llama is None:
        raise Fail("llama-server.exe introuvable : indiquez le build CUDA avec --llama-dir "
                   f"(essayé : {llama_src}).")
    llama_cpu = fetch_llama(Path(args.llama_cpu_dir), STAGING / "llama-cpu", "build processeur", False) \
        if args.llama_cpu_dir else None
    if llama_cpu is None:
        shutil.rmtree(STAGING / "llama-cpu", ignore_errors=True)
        warn("pas de build processeur de secours (--llama-cpu-dir) : sans GPU utilisable, l'OCR ne démarrera pas. "
             "Le build CUDA est essayé aussi avec -ngl 0 (processeur) tant que ses DLL CUDA sont présentes.")

    step("Assemblage du colis")
    m = assemble(out, lock=lock, torch=torch, wheels=wheels, models=models, py_installer=py_installer,
                 vcredist=vcredist, llama=llama, llama_cpu=llama_cpu, frontend=front, models_report=report,
                 skip_hash=args.skip_hash)

    step("Récapitulatif")
    info(f"{out}  {human(m['size'])}  {m['wheels']} roues, {m['packages']} paquets")
    info(f"interface : {'oui' if m['frontend_bundled'] else 'NON'} ; modèles : {json.dumps(report, ensure_ascii=False)}")
    info(f"durée : {time.time() - started:.0f} s ; cache réutilisable : {STAGING.name}/ ({human(dir_size(STAGING))})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Fail as exc:
        print(f"\n[ERREUR] {exc}", flush=True)
        raise SystemExit(1) from None
