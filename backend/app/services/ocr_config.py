"""Configuration effective du moteur OCR : réglages de l'administrateur (table settings), sinon backend/.env.

Le worker la relit avant chaque document : un changement fait dans l'interface (chemin de llama.cpp, largeur des
pages, nombre de slots...) s'applique au document suivant, sans redémarrer. Si la base est illisible, on retombe
sur backend/.env plutôt que de bloquer l'OCR.
"""
from __future__ import annotations

import logging
from dataclasses import astuple, dataclass, fields
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings

log = logging.getLogger("ocr.config")


@dataclass(frozen=True)
class OcrConfig:
    backend: str = "surya2"
    llama_cpp_dir: Path | None = None
    llama_cpp_cpu_dir: Path | None = None
    llama_port: int = 8091
    llama_parallel: int = 1
    llama_ngl: int = 99
    llama_ctx_size: int = 16384
    cpu_fallback: bool = True
    page_width: int = 0
    block_token_ceiling: int = 8192
    layout_refine: bool = True
    line_detector_device: str = "cpu"
    stamp_erase: bool = True
    layout_heron: bool = True
    second_reader: bool = True
    second_reader_threshold: float = 0.9
    gguf_repo: str = "datalab-to/surya-ocr-2-gguf"

    # Réglages lus au démarrage du moteur : les changer impose de le redémarrer (fait par le worker).
    ENGINE_FIELDS = ("llama_cpp_dir", "llama_cpp_cpu_dir", "llama_port", "llama_parallel", "llama_ngl",
                     "llama_ctx_size", "cpu_fallback", "block_token_ceiling", "layout_refine", "line_detector_device",
                     "layout_heron", "gguf_repo")

    def engine_key(self) -> tuple:
        return tuple(getattr(self, f) for f in self.ENGINE_FIELDS)

    @classmethod
    def from_env(cls) -> "OcrConfig":
        s = get_settings()
        return cls(backend=s.ocr_backend, llama_cpp_dir=s.llama_cpp_dir, llama_cpp_cpu_dir=s.llama_cpp_cpu_dir,
                   llama_port=s.llama_port, llama_parallel=max(1, s.llama_parallel), llama_ngl=s.llama_ngl,
                   llama_ctx_size=s.llama_ctx_size, cpu_fallback=s.cpu_fallback, page_width=s.page_width,
                   block_token_ceiling=s.block_token_ceiling, layout_refine=s.layout_refine,
                   line_detector_device=s.line_detector_device, stamp_erase=s.stamp_erase,
                   layout_heron=s.layout_heron, second_reader=s.second_reader,
                   second_reader_threshold=s.second_reader_threshold, gguf_repo=s.gguf_repo)

    @classmethod
    def load(cls, db: Session | None = None) -> "OcrConfig":
        """Réglages de l'administrateur par-dessus backend/.env. Jamais d'exception : au pire, backend/.env."""
        from app.services import app_settings

        base = cls.from_env()
        try:
            if db is None:
                from app.core.db import SessionLocal

                with SessionLocal() as own:
                    values = app_settings.get_all(own)
            else:
                values = app_settings.get_all(db)
        except Exception as e:  # noqa: BLE001
            log.warning("réglages du moteur OCR illisibles en base (%s) : valeurs de backend/.env", type(e).__name__)
            return base
        path = lambda v: Path(v) if v else None  # noqa: E731
        return cls(backend=base.backend, llama_cpp_dir=path(values["llama_cpp_dir"]),
                   llama_cpp_cpu_dir=path(values["llama_cpp_cpu_dir"]), llama_port=base.llama_port,
                   llama_parallel=values["llama_parallel"], llama_ngl=values["llama_ngl"],
                   llama_ctx_size=values["llama_ctx_size"], cpu_fallback=values["cpu_fallback"],
                   page_width=values["page_width"], block_token_ceiling=values["block_token_ceiling"],
                   layout_refine=values["layout_refine"], line_detector_device=values["line_detector_device"],
                   stamp_erase=values["stamp_erase"], layout_heron=values["layout_heron"],
                   second_reader=values["second_reader"],
                   second_reader_threshold=values["second_reader_threshold"], gguf_repo=base.gguf_repo)


def describe(cfg: OcrConfig) -> dict:
    """Vue sérialisable (diagnostic, journal) : aucun secret dans ces réglages."""
    return {f.name: (str(v) if isinstance(v, Path) else v) for f, v in zip(fields(cfg), astuple(cfg))}
