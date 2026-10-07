"""Configuration par variables d'environnement (fichier .env facultatif). Aucun secret dans le dépôt."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SECRET = "changez-moi-avant-la-mise-en-production-0000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_prefix="OCR_", extra="ignore")

    # Base : MariaDB en production (mysql+pymysql://...), SQLite accepté pour les tests.
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'app.db').as_posix()}"
    data_dir: Path = BACKEND_DIR / "data"

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

    # Services locaux
    ollama_url: str = "http://127.0.0.1:11434"
    llama_cpp_dir: Path | None = None  # dossier Release de llama.cpp (contient llama-server.exe)
    llama_port: int = 8081
    gguf_repo: str = "datalab-to/surya-ocr-2-gguf"
    # Requêtes OCR simultanées (blocs d'une page traités en parallèle). 1 sur 4 Go ; 2 à 4 sur une RTX 3060 12 Go :
    # chaque slot ajoute un contexte de 16k tokens en VRAM.
    llama_parallel: int = 1
    # Recalage de la mise en page sur les lignes détectées (détecteur de lignes de Surya, poids dans le cache datalab).
    layout_refine: bool = True
    line_detector_device: str = "cpu"  # cpu (ne prend pas de VRAM) | cuda
    # Lire la page sans l'encre bleue / rouge des tampons et signatures (repérés par leur couleur).
    stamp_erase: bool = True

    # Worker
    worker_poll_seconds: float = 2.0
    job_stale_minutes: int = 30
    ocr_backend: str = "surya2"  # surya2 | dummy

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
