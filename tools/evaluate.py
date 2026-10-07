"""Évalue un backend OCR sur un lot de pages et n'écrit qu'un RAPPORT CHIFFRÉ.

Confidentialité (PLAN.md §4 bis) : le rapport ne contient ni texte extrait, ni image, ni nom de
fichier, ni chemin. Les pages sont désignées par leur rang. Les erreurs sont réduites à leur type.
Cet outil est conçu pour être lancé par l'utilisateur sur ses vrais scans ; seul le rapport JSON
est communiqué à Claude Code.

Entrée : dossier d'images / PDF, ou fichier unique.
Référence facultative (--reference DOSSIER) : un fichier <nom_de_page>.txt par page (transcription
exacte) et/ou <nom_de_page>.json {"tables": n, "images": n} pour mesurer la détection.
Les noms de pages sont ceux produits par degrade.py (<nom>_pNNN) ou, pour une image seule, son nom.

    python tools/evaluate.py samples/degrades --backend surya1 --sortie rapports/surya1.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

from PIL import Image
from rapidfuzz.distance import Levenshtein

sys.path.insert(0, str(Path(__file__).parent))
from degrade import IMAGE_EXT, load_images  # noqa: E402
from ocr_backends import BACKENDS, PageResult  # noqa: E402

TABLE_LABELS = {"Table"}
IMAGE_LABELS = {"Picture", "Figure", "Diagram", "Image"}


# ---------- mesures de texte ----------

def is_arabic(ch: str) -> bool:
    o = ord(ch)
    return (0x0600 <= o <= 0x06FF) or (0x0750 <= o <= 0x077F) or (0x08A0 <= o <= 0x08FF) or (0xFB50 <= o <= 0xFDFF) or (0xFE70 <= o <= 0xFEFF)


def is_latin(ch: str) -> bool:
    return ch.isalpha() and unicodedata.name(ch, "").startswith("LATIN")


def normalize(s: str) -> str:
    """Normalisation neutre : NFKC, suppression des diacritiques arabes (tashkeel) et des espaces multiples."""
    s = unicodedata.normalize("NFKC", s)
    s = "".join(c for c in s if not (0x064B <= ord(c) <= 0x065F or ord(c) == 0x0670 or ord(c) == 0x0640))
    return " ".join(s.split())


def script_ratio(text: str) -> tuple[int, int, int]:
    """(lettres arabes, lettres latines, lettres totales)."""
    letters = [c for c in text if c.isalpha()]
    return sum(map(is_arabic, letters)), sum(map(is_latin, letters)), len(letters)


def word_overlap(ref: str, hyp: str) -> dict[str, float | None]:
    """Rappel / précision des mots, insensibles à l'ordre (colonnes, en-têtes) : complète le CER."""
    from collections import Counter

    r, h = Counter(normalize(ref).split()), Counter(normalize(hyp).split())
    common = sum((r & h).values())
    nr, nh = sum(r.values()), sum(h.values())
    return {"rappel_mots": common / nr if nr else None, "precision_mots": common / nh if nh else None}


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC_VARIANTS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})


def canonical(s: str) -> str:
    """Forme canonique pour comparer sans les artefacts de la référence (couche texte d'un PDF) :
    lettres et chiffres seulement (ni espaces ni ponctuation : la couche texte coupe les mots arabes, « رشي د »),
    chiffres arabes-indiens → ASCII, variantes d'alef / ya / ta marbuta unifiées, minuscules."""
    s = normalize(s).translate(_DIGITS).translate(_ARABIC_VARIANTS).lower()
    return "".join(c for c in s if c.isalnum())


def ngram_overlap(ref: str, hyp: str, n: int = 3) -> dict[str, float | None]:
    """Rappel / précision sur les trigrammes de caractères de la forme canonique : insensible à l'ordre des
    blocs (colonnes) comme aux espaces mal placés de la référence. Mesure principale pour l'arabe."""
    from collections import Counter

    def grams(t: str) -> Counter:
        c = canonical(t)
        return Counter(c[i:i + n] for i in range(len(c) - n + 1))

    r, h = grams(ref), grams(hyp)
    common = sum((r & h).values())
    nr, nh = sum(r.values()), sum(h.values())
    return {"rappel_car": common / nr if nr else None, "precision_car": common / nh if nh else None}


def cer(ref: str, hyp: str) -> float | None:
    return Levenshtein.distance(ref, hyp) / len(ref) if ref else None


def cer_by_script(ref: str, hyp: str) -> dict[str, float | None]:
    """CER global, et sur le sous-texte arabe / latin seulement (les autres caractères sont ignorés)."""
    ref, hyp = normalize(ref), normalize(hyp)

    def keep(s: str, pred) -> str:
        return "".join(c for c in s if pred(c) or c == " ")

    out = {"global": cer(ref, hyp)}
    for name, pred in (("arabe", is_arabic), ("latin", is_latin)):
        r = keep(ref, pred)
        out[name] = cer(" ".join(r.split()), " ".join(keep(hyp, pred).split())) if r.strip() else None
    return out


# ---------- VRAM ----------

class VramSampler:
    """Relève la VRAM utilisée (nvidia-smi) dans un thread ; ne conserve que le maximum en Mo."""

    def __init__(self, interval: float = 0.5):
        self.interval, self.peak, self.baseline, self._stop = interval, None, None, threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @staticmethod
    def _read() -> int | None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip().splitlines()
            return max(int(x) for x in out) if out else None
        except Exception:  # noqa: BLE001 - pas de GPU / nvidia-smi absent
            return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            v = self._read()
            if v is not None and (self.peak is None or v > self.peak):
                self.peak = v
            self._stop.wait(self.interval)

    def __enter__(self):
        self.baseline = self._read()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)


# ---------- évaluation ----------

def page_text(res: PageResult) -> str:
    return " ".join(b.text for b in res.blocks if b.text)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def round_or_none(x, n=4):
    return None if x is None else round(x, n)


def summarize(values: list[float]) -> dict:
    return {
        "n": len(values),
        "moyenne": round_or_none(statistics.fmean(values)) if values else None,
        "mediane": round_or_none(statistics.median(values)) if values else None,
        "p90": round_or_none(percentile(values, 0.9)),
    }


def evaluate(backend, pages: list[tuple[str, Image.Image]], reference: Path | None, warmup: bool) -> dict:
    per_page: list[dict] = []
    all_conf: list[float] = []
    label_counts: dict[str, int] = {}
    cers: dict[str, list[float]] = {"global": [], "arabe": [], "latin": []}
    words: dict[str, list[float]] = {"rappel_mots": [], "precision_mots": [], "rappel_car": [], "precision_car": []}
    det_err = {"tables": [], "images": []}
    ar = lat = tot = 0
    times: list[float] = []
    matched = 0

    load_start = time.perf_counter()
    backend.load()
    load_seconds = time.perf_counter() - load_start

    if warmup and pages:  # la 1re page paie les initialisations CUDA ; on la fait hors mesure
        try:
            backend.run(pages[0][1])
        except Exception:  # noqa: BLE001
            pass

    with VramSampler() as vram:
        for rank, (name, img) in enumerate(pages, start=1):
            rec: dict = {"page": rank}
            t0 = time.perf_counter()
            try:
                res = backend.run(img)
            except Exception as e:  # noqa: BLE001 - jamais le message : il pourrait contenir du contenu
                rec["erreur"] = type(e).__name__
                per_page.append(rec)
                continue
            dt = time.perf_counter() - t0
            times.append(dt)
            text = page_text(res)
            a, l, t = script_ratio(text)
            ar, lat, tot = ar + a, lat + l, tot + t
            confs = [b.confidence for b in res.blocks if b.confidence is not None]
            all_conf.extend(confs)
            labels: dict[str, int] = {}
            for b in res.blocks:
                labels[b.label] = labels.get(b.label, 0) + 1
                label_counts[b.label] = label_counts.get(b.label, 0) + 1
            rec.update({
                "secondes": round(dt, 2),
                "blocs": len(res.blocks),
                "caracteres": len(text),
                "confiance_moyenne": round_or_none(statistics.fmean(confs)) if confs else None,
                "part_arabe": round_or_none(a / t if t else None),
                "tableaux": sum(labels.get(k, 0) for k in TABLE_LABELS),
                "images": sum(labels.get(k, 0) for k in IMAGE_LABELS),
            })
            if reference is not None:
                ref_txt = reference / f"{name}.txt"
                if ref_txt.is_file():
                    matched += 1
                    ref_text = ref_txt.read_text(encoding="utf-8")
                    wo = {**word_overlap(ref_text, text), **ngram_overlap(ref_text, text)}
                    for k, v in wo.items():
                        if v is not None:
                            words[k].append(v)
                    rec["mots"] = {k: round_or_none(v) for k, v in wo.items()}
                    c = cer_by_script(ref_text, text)
                    for k, v in c.items():
                        if v is not None:
                            cers[k].append(v)
                    rec["cer"] = {k: round_or_none(v) for k, v in c.items()}
                ref_json = reference / f"{name}.json"
                if ref_json.is_file():
                    meta = json.loads(ref_json.read_text(encoding="utf-8"))
                    for key, got in (("tables", rec["tableaux"]), ("images", rec["images"])):
                        if key in meta:
                            det_err[key].append(abs(got - int(meta[key])))
            per_page.append(rec)

    backend.close()
    ok = [p for p in per_page if "erreur" not in p]
    report = {
        "backend": backend.name,
        "environnement": {"python": platform.python_version(), "os": platform.system()},
        "pages": {"total": len(per_page), "reussies": len(ok), "en_erreur": len(per_page) - len(ok)},
        "erreurs_par_type": _count([p["erreur"] for p in per_page if "erreur" in p]),
        "temps": {
            "chargement_modele_s": round(load_seconds, 2),
            "secondes_par_page": summarize(times),
        },
        "vram_mo": {"avant": vram.baseline, "pic": vram.peak,
                    "surcout": (vram.peak - vram.baseline) if vram.peak is not None and vram.baseline is not None else None},
        "confiance": summarize(all_conf),
        "blocs_par_etiquette": dict(sorted(label_counts.items())),
        "ecritures": {"part_arabe": round_or_none(ar / tot if tot else None),
                      "part_latine": round_or_none(lat / tot if tot else None)},
        "references_appariees": matched if reference is not None else None,
        "cer": {k: summarize(v) for k, v in cers.items()} if reference is not None else None,
        "mots": {k: summarize(v) for k, v in words.items()} if reference is not None else None,
        "detection_ecart_absolu_moyen": ({k: round_or_none(statistics.fmean(v)) if v else None for k, v in det_err.items()}
                                         if reference is not None else None),
        "detail_pages": per_page,
    }
    return report


def _count(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def make_backend(name: str):
    """Backend intégré, ou module externe tools/ocr_backend_<name>.py (classe Backend) : un modèle par environnement."""
    if name in BACKENDS:
        return BACKENDS[name]()
    import importlib

    try:
        return importlib.import_module(f"ocr_backend_{name}").Backend()
    except ModuleNotFoundError as e:
        raise SystemExit(f"Backend inconnu : {name}") from e


def load_pages(path: Path, dpi: int) -> list[tuple[str, Image.Image]]:
    import cv2

    return [(n, Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))) for n, bgr in load_images(path, dpi, suffix_images=False)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entree", type=Path)
    ap.add_argument("--backend", required=True, help="dummy | surya1 | surya2 | <nom> (module tools/ocr_backend_<nom>.py exposant la classe Backend)")
    ap.add_argument("--sortie", type=Path, required=True, help="rapport JSON (chiffres uniquement)")
    ap.add_argument("--reference", type=Path, help="dossier de transcriptions de référence (facultatif)")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--max-pages", type=int, default=0, help="0 = toutes")
    ap.add_argument("--sans-echauffement", action="store_true")
    ap.add_argument("--llama-cpp-dir", type=Path, help="dossier Release de llama.cpp (contient llama-server.exe) ; sinon variable LLAMA_CPP_DIR")
    args = ap.parse_args(argv)
    if args.llama_cpp_dir:
        os.environ["LLAMA_CPP_DIR"] = str(args.llama_cpp_dir)

    if not args.entree.exists():
        print("Entrée introuvable.", file=sys.stderr)
        return 2
    pages = load_pages(args.entree, args.dpi)
    if args.max_pages:
        pages = pages[: args.max_pages]
    if not pages:
        print("Aucune page lisible.", file=sys.stderr)
        return 2

    backend = make_backend(args.backend)
    report = evaluate(backend, pages, args.reference, warmup=not args.sans_echauffement)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    p = report["pages"]
    print(f"{p['reussies']}/{p['total']} page(s) traitée(s) -> rapport : {args.sortie}")
    return 0 if p["reussies"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
