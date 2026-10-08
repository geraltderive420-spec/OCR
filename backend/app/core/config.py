"""Configuration par variables d'environnement (fichier .env facultatif). Aucun secret dans le dépôt."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SECRET = "changez-moi-avant-la-mise-en-production-0000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_prefix="OCR_", extra="ignore")

    # Base SQLite embarquée (aucun serveur de base à installer) : <data_dir>/db/ocr.sqlite3 si rien n'est précisé.
    database_url: str = ""
    data_dir: Path = BACKEND_DIR / "data"

    # Service web (python -m app.serve) : HTTPS si un certificat est fourni.
    api_host: str = "0.0.0.0"  # tout le réseau ; 127.0.0.1 pour un accès local seulement
    api_port: int = 8085
    # Interface sur son propre port (0 = servie par l'API seulement) : python -m app.frontend relaie /api vers l'API.
    frontend_port: int = 3050
    frontend_host: str = ""  # défaut : api_host
    ssl_certfile: Path | None = None
    ssl_keyfile: Path | None = None

    # Sécurité
    secret_key: str = Field(default=DEFAULT_SECRET, min_length=32)
    access_token_minutes: int = 15
    refresh_token_hours: int = 12
    cookie_secure: bool = False  # True derrière HTTPS (production)
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # Limites d'upload (modifiables ensuite dans les paramètres administrateur)
    max_upload_mb: int = 100
    max_pages: int = 300

    # Services locaux. Les réglages du moteur OCR ci-dessous sont les valeurs PAR DÉFAUT : l'administrateur les
    # remplace dans Administration > Moteur OCR (table settings), sans redémarrer.
    ollama_url: str = "http://127.0.0.1:11434"
    llama_cpp_dir: Path | None = None  # dossier de llama.cpp contenant llama-server.exe (build CUDA)
    llama_cpp_cpu_dir: Path | None = None  # build processeur de secours (aucun GPU utilisable)
    llama_port: int = 8091  # le second lecteur prend llama_port + 1
    llama_host: str = "127.0.0.1"  # adresse d'écoute de llama-server (0.0.0.0 : tout le réseau) ; le worker s'y connecte en local
    llama_ngl: int = 99  # couches du modèle sur le GPU (99 = toutes)
    llama_ctx_size: int = 16384  # contexte par requête simultanée
    cpu_fallback: bool = True  # sans GPU utilisable : OCR sur le processeur (lent) plutôt qu'un échec
    page_width: int = 0  # largeur des pages données à Surya, en pixels (0 = rendu à 200 dpi)
    block_token_ceiling: int = 8192  # plafond de tokens générés par bloc (SURYA_MAX_TOKENS_BLOCK_CEILING)
    gguf_repo: str = "datalab-to/surya-ocr-2-gguf"
    # Requêtes OCR simultanées (blocs d'une page traités en parallèle). 1 sur 4 Go ; 2 à 4 sur une RTX 3060 12 Go :
    # chaque slot ajoute un contexte de 16k tokens en VRAM.
    llama_parallel: int = 1
    # Recalage de la mise en page sur les lignes détectées (détecteur de lignes de Surya, poids dans le cache datalab).
    layout_refine: bool = True
    line_detector_device: str = "cpu"  # cpu (ne prend pas de VRAM) | cuda
    # Lire la page sans l'encre bleue / rouge des tampons et signatures (repérés par leur couleur).
    stamp_erase: bool = True
    # Second avis sur la mise en page : détecteur Heron de Docling (processeur, poids dans le cache Hugging Face).
    layout_heron: bool = True
    # Seconde lecture des blocs douteux par PaddleOCR-VL 1.5 (second llama-server, port llama_port + 1).
    second_reader: bool = True
    second_reader_threshold: float = 0.9  # relire les blocs dont la confiance de Surya est sous ce seuil

    # Worker
    worker_poll_seconds: float = 2.0
    job_stale_minutes: int = 30
    ocr_backend: str = "surya2"  # surya2 | dummy

    @model_validator(mode="after")
    def _default_database(self) -> "Settings":
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'db' / 'ocr.sqlite3').as_posix()}"
        return self

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def pages_dir(self) -> Path:
        return self.data_dir / "pages"

    @property
    def crops_dir(self) -> Path:
        return self.data_dir / "crops"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
