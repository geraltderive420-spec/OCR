"""Gestion du GPU partagé : VRAM libre et cycle de vie de `llama-server` (démarré à la demande, arrêté par PID).

Surya sait lancer son propre serveur mais pas l'arrêter sous Windows, et garde une trace périmée qui provoque des
« Connection error » : c'est donc le worker qui lance et arrête le processus (PLAN.md §3).
Ne jamais arrêter `llama-server.exe` par son nom : ce serait couper celui d'une autre application.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

from app.core.config import get_settings

log = logging.getLogger("ocr.gpu")
MODEL_ALIAS = "datalab-to/surya-ocr-2"  # nom que Surya vérifie auprès du serveur
CTX_SIZE = 16384


def free_vram_mb() -> int | None:
    """VRAM libre en Mo sur le GPU le moins chargé, ou None si indisponible (pas de GPU / nvidia-smi absent)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8, check=True,
        ).stdout.split()
        return max(int(x) for x in out) if out else None
    except Exception:  # noqa: BLE001
        return None


class LlamaServerError(RuntimeError):
    """Erreur du serveur OCR. `permanent` = mauvaise configuration (inutile de réessayer), sinon situation passagère."""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _gguf_paths() -> tuple[str, str]:
    """GGUF du modèle et du projecteur, depuis le cache Hugging Face (hors ligne : jamais de téléchargement ici)."""
    from huggingface_hub import hf_hub_download

    repo = get_settings().gguf_repo
    try:
        return (hf_hub_download(repo, "surya-2.gguf", local_files_only=True),
                hf_hub_download(repo, "surya-2-mmproj.gguf", local_files_only=True))
    except Exception as e:  # noqa: BLE001
        raise LlamaServerError("Fichiers GGUF de Surya 2 absents du cache local.", permanent=True) from e


def find_binary() -> Path:
    d = get_settings().llama_cpp_dir
    if d is None:
        raise LlamaServerError("OCR_LLAMA_CPP_DIR n'est pas défini (dossier Release de llama.cpp).", permanent=True)
    exe = Path(d) / ("llama-server.exe" if os.name == "nt" else "llama-server")
    if not exe.is_file():
        raise LlamaServerError("llama-server introuvable dans OCR_LLAMA_CPP_DIR.", permanent=True)
    return exe


class LlamaServer:
    """Processus `llama-server` possédé par le worker."""

    def __init__(self, port: int | None = None, startup_timeout: float = 300.0, build_cmd=None):
        """`build_cmd(port) -> list[str]` remplace la commande llama-server (tests avec un faux serveur)."""
        self._build_cmd = build_cmd
        self.port = port or get_settings().llama_port
        self.startup_timeout = startup_timeout
        self.proc: subprocess.Popen | None = None
        self._log_fp = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def healthy(self) -> bool:
        try:
            return httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _llama_cmd(self) -> list[str]:
        model, mmproj = _gguf_paths()
        s = get_settings()
        parallel = max(1, s.llama_parallel)
        cmd = [str(find_binary()), "-m", model, "--mmproj", mmproj, "-ngl", os.environ.get("LLAMA_CPP_NGL", "99"),
               "--host", "127.0.0.1", "--port", str(self.port), "--parallel", str(parallel),
               "--ctx-size", str(CTX_SIZE * parallel),  # le contexte est partagé entre les slots
               "--alias", MODEL_ALIAS, "--jinja"]
        if os.environ.get("LLAMA_CPP_NO_MMPROJ_OFFLOAD", "").lower() in {"1", "true"}:
            cmd.append("--no-mmproj-offload")
        return cmd

    def start(self) -> None:
        if self.running() and self.healthy():
            return
        self.stop()
        if self.healthy():
            raise LlamaServerError(f"Le port {self.port} est déjà occupé par un autre serveur.")
        cmd = self._build_cmd(self.port) if self._build_cmd else self._llama_cmd()
        logdir = get_settings().data_dir / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        self._log_fp = open(logdir / "llama-server.log", "ab")  # journal technique : aucun contenu de document
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdout=self._log_fp, stderr=subprocess.STDOUT, creationflags=flags)
        log.info("llama-server démarré (pid %s, port %s)", self.proc.pid, self.port)
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self._close_log()
                raise LlamaServerError(f"llama-server s'est arrêté au démarrage (code {self.proc.returncode}).")
            if self.healthy():
                return
            time.sleep(1.0)
        self.stop()
        raise LlamaServerError("llama-server n'a pas répondu à temps.")

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=15)
            log.info("llama-server arrêté (pid %s)", p.pid)
        self._close_log()

    def _close_log(self) -> None:
        if self._log_fp:
            try:
                self._log_fp.close()
            finally:
                self._log_fp = None
