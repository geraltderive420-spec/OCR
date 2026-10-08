"""Contrôles de l'installation du moteur OCR, sans rien démarrer : binaires, modèles locaux, GPU, base, worker.

Utilisé par l'auto-diagnostic (python -m app.cli check) et par Administration > Moteur OCR / Services.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.core.config import get_settings
from app.services import gpu
from app.services.ocr_config import OcrConfig

DETECTOR_CHECKPOINT = "text_detection/2025_05_07"  # surya.settings.DETECTOR_MODEL_CHECKPOINT (surya-ocr 0.22.1)
HERON_REPO = "docling-project/docling-layout-heron"


def model_cache_dir() -> Path:
    """Cache des modèles « datalab » de Surya (détecteur de lignes) : MODEL_CACHE_DIR, sinon le cache utilisateur."""
    env = os.environ.get("MODEL_CACHE_DIR")
    if env:
        return Path(env)
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("datalab")) / "models"


def line_detector_present() -> bool:
    d = model_cache_dir() / DETECTOR_CHECKPOINT
    return (d / "model.safetensors").is_file() or (d.is_dir() and any(p.suffix == ".safetensors" for p in d.iterdir()))


def heron_present() -> bool:
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(HERON_REPO, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def surya_gguf_present(cfg: OcrConfig) -> bool:
    try:
        gpu._gguf_paths(cfg.gguf_repo)
        return True
    except gpu.LlamaServerError:
        return False


def second_reader_present() -> bool:
    from app.services.ocr import second_reader

    return second_reader.available()


def binary_report(directory: Path | str | None) -> dict:
    """{configured, path, ok, version} : le binaire existe-t-il et démarre-t-il (DLL CUDA / Visual C++ présentes) ?"""
    if not directory:
        return {"configured": False, "path": None, "ok": False, "version": "non configuré"}
    exe = gpu.locate_binary(directory)
    if exe is None:
        return {"configured": True, "path": None, "ok": False, "version": "llama-server introuvable dans ce dossier"}
    ok, version = gpu.binary_version(exe)
    return {"configured": True, "path": str(exe), "ok": ok, "version": version}


def models_report(cfg: OcrConfig) -> dict:
    return {"surya_gguf": surya_gguf_present(cfg), "line_detector": line_detector_present(),
            "heron": heron_present(), "second_reader": second_reader_present()}


def database_report() -> dict:
    from app.core.db import sqlite_path

    s = get_settings()
    path = sqlite_path(s.database_url)
    out = {"engine": "sqlite" if s.database_url.startswith("sqlite") else s.database_url.split(":", 1)[0]}
    if path is not None:
        out["path"] = str(path)
        try:
            out["size_mb"] = round(sum(p.stat().st_size for p in path.parent.glob(path.name + "*")) / 2**20, 1)
        except OSError:
            out["size_mb"] = None
    return out


def offline_report() -> bool:
    return os.environ.get("HF_HUB_OFFLINE") == "1"
