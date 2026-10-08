"""Gestion du GPU partagé : VRAM libre et cycle de vie de `llama-server` (démarré à la demande, arrêté par PID).

Surya sait lancer son propre serveur mais pas l'arrêter sous Windows, et garde une trace périmée qui provoque des
« Connection error » : c'est donc le worker qui lance et arrête le processus (PLAN.md §3).
Ne jamais arrêter `llama-server.exe` par son nom : ce serait couper celui d'une autre application. Le seul processus
qu'on se permet d'arrêter sans l'avoir lancé dans ce processus Python est un orphelin d'un worker précédent (tué
brutalement) : son PID est noté dans <data_dir>/run et on vérifie que c'est bien un llama-server avant d'agir.

Solutions de repli, dans l'ordre : build CUDA sur le GPU → même build sur le processeur (-ngl 0) → build processeur
de secours. Le repli processeur est lent mais évite qu'un pilote absent ou une carte en panne bloque tout.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.winjob import kill_with_parent
from app.services.ocr_config import OcrConfig

log = logging.getLogger("ocr.gpu")
MODEL_ALIAS = "datalab-to/surya-ocr-2"  # nom que Surya vérifie auprès du serveur
CTX_SIZE = 16384
EXE = "llama-server.exe" if os.name == "nt" else "llama-server"
# Sous-dossiers essayés quand l'administrateur indique la racine de llama.cpp plutôt que le dossier du binaire.
SUBDIRS = ("", "build/bin/Release", "build/bin", "bin/Release", "bin", "Release")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _nvidia_smi(query: str) -> list[list[str]] | None:
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8, check=True, creationflags=NO_WINDOW).stdout
        rows = [[c.strip() for c in line.split(",")] for line in out.splitlines() if line.strip()]
        return rows or None
    except Exception:  # noqa: BLE001
        return None


def free_vram_mb() -> int | None:
    """VRAM libre en Mo sur le GPU le moins chargé, ou None si indisponible (pas de GPU / nvidia-smi absent)."""
    rows = _nvidia_smi("memory.free")
    try:
        return max(int(r[0]) for r in rows) if rows else None
    except ValueError:
        return None


def gpu_info() -> list[dict] | None:
    """Cartes NVIDIA visibles (nom, VRAM totale / libre, pilote), pour l'écran Services."""
    rows = _nvidia_smi("name,memory.total,memory.free,driver_version")
    if not rows:
        return None
    out = []
    for r in rows:
        try:
            out.append({"name": r[0], "total_mb": int(r[1]), "free_mb": int(r[2]), "driver": r[3]})
        except (IndexError, ValueError):
            continue
    return out or None


class LlamaServerError(RuntimeError):
    """Erreur du serveur OCR. `permanent` = mauvaise configuration (inutile de réessayer), sinon situation passagère."""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _gguf_paths(repo: str | None = None) -> tuple[str, str]:
    """GGUF du modèle et du projecteur, depuis le cache Hugging Face (hors ligne : jamais de téléchargement ici)."""
    from huggingface_hub import hf_hub_download

    repo = repo or get_settings().gguf_repo
    try:
        return (hf_hub_download(repo, "surya-2.gguf", local_files_only=True),
                hf_hub_download(repo, "surya-2-mmproj.gguf", local_files_only=True))
    except Exception as e:  # noqa: BLE001
        raise LlamaServerError("Fichiers GGUF de Surya 2 absents du cache local.", permanent=True) from e


def locate_binary(directory: Path | str | None) -> Path | None:
    """llama-server dans le dossier indiqué, ou dans ses sous-dossiers de compilation habituels."""
    if not directory:
        return None
    d = Path(directory)
    if d.is_file() and d.name.lower() == EXE.lower():
        return d
    for sub in SUBDIRS:
        exe = d / sub / EXE if sub else d / EXE
        if exe.is_file():
            return exe
    return None


def find_binary(directory: Path | str | None = None) -> Path:
    if directory is None:
        directory = get_settings().llama_cpp_dir
    if not directory:
        raise LlamaServerError("Chemin de llama.cpp non défini (Administration > Moteur OCR, ou OCR_LLAMA_CPP_DIR).",
                               permanent=True)
    exe = locate_binary(directory)
    if exe is None:
        raise LlamaServerError("llama-server introuvable dans le dossier de llama.cpp configuré "
                               "(Administration > Moteur OCR, ou OCR_LLAMA_CPP_DIR).", permanent=True)
    return exe


def binary_version(exe: Path, timeout: float = 20.0) -> tuple[bool, str]:
    """(démarre ?, première ligne utile de `--version`). Un binaire dont une DLL manque échoue ici, pas en plein OCR."""
    try:
        p = subprocess.run([str(exe), "--version"], capture_output=True, text=True, errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "pas de réponse à --version"
    except OSError as e:
        return False, f"lancement impossible ({type(e).__name__})"
    text = (p.stdout + "\n" + p.stderr).strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    version = next((ln for ln in lines if ln.lower().startswith("version")), lines[0] if lines else "")
    if p.returncode != 0:
        # 0xC0000135 : DLL introuvable (runtime CUDA ou Visual C++ absent).
        hint = " (DLL manquante : runtime CUDA ou Visual C++)" if p.returncode in (-1073741515, 3221225781) else ""
        return False, f"code {p.returncode}{hint}"
    cuda = any("cuda" in ln.lower() for ln in lines)
    return True, version + (" — CUDA" if cuda else "")


# ------------------------------------------------------------------ orphelins (worker tué sans arrêt propre)

def _pid_file(port: int) -> Path:
    return get_settings().data_dir / "run" / f"llama-server-{port}.pid"


def _image_name(pid: int) -> str | None:
    """Nom de l'exécutable d'un PID (Windows : tasklist), None s'il n'existe plus ou si on ne sait pas le dire."""
    if os.name != "nt":
        try:
            return Path(os.readlink(f"/proc/{pid}/exe")).name
        except OSError:
            return None
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, errors="replace", timeout=10, creationflags=NO_WINDOW).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    if not out.startswith('"'):
        return None  # « INFO: Aucune tâche… »
    return out.split('","')[0].strip('"')


def kill_orphan(port: int) -> bool:
    """Arrête le llama-server laissé par un worker précédent sur ce port (PID noté, nom vérifié)."""
    f = _pid_file(port)
    try:
        pid = int(f.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    name = _image_name(pid)
    if not name or not name.lower().startswith("llama-server"):
        f.unlink(missing_ok=True)  # PID réattribué à un autre programme : on n'y touche pas
        return False
    log.warning("llama-server orphelin (pid %s, port %s) : arrêt", pid, port)
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15,
                           creationflags=NO_WINDOW)
        else:
            os.kill(pid, 9)
    except Exception:  # noqa: BLE001
        return False
    f.unlink(missing_ok=True)
    time.sleep(1.0)
    return True


class LlamaServer:
    """Processus `llama-server` possédé par le worker."""

    def __init__(self, port: int | None = None, startup_timeout: float = 300.0, build_cmd=None,
                 model_files=None, alias: str = MODEL_ALIAS, ctx_size: int | None = None,
                 log_name: str = "llama-server", config: OcrConfig | None = None):
        """`build_cmd(port) -> list[str]` remplace la commande llama-server (tests avec un faux serveur).

        `model_files() -> (gguf, mmproj)` choisit un autre modèle que Surya 2 (second lecteur), avec son `alias`.
        """
        self.config = config or OcrConfig.from_env()
        self._build_cmd = build_cmd
        self._model_files = model_files or (lambda: _gguf_paths(self.config.gguf_repo))
        self.alias, self.log_name = alias, log_name
        self.ctx_size = ctx_size or self.config.llama_ctx_size or CTX_SIZE
        self.port = port or self.config.llama_port
        self.startup_timeout = startup_timeout
        self.proc: subprocess.Popen | None = None
        self.mode = "gpu"  # gpu | cpu : sur quoi tourne le serveur démarré
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

    def _llama_cmd(self, exe: Path, ngl: int) -> list[str]:
        model, mmproj = self._model_files()
        parallel = max(1, self.config.llama_parallel)
        cmd = [str(exe), "-m", model, "--mmproj", mmproj, "-ngl", os.environ.get("LLAMA_CPP_NGL", str(ngl)),
               "--host", get_settings().llama_host or "127.0.0.1", "--port", str(self.port), "--parallel", str(parallel),
               "--ctx-size", str(self.ctx_size * parallel),  # le contexte est partagé entre les slots
               "--alias", self.alias, "--jinja"]
        if ngl == 0 or os.environ.get("LLAMA_CPP_NO_MMPROJ_OFFLOAD", "").lower() in {"1", "true"}:
            cmd.append("--no-mmproj-offload")
        return cmd

    def _attempts(self) -> list[tuple[str, list[str]]]:
        """Commandes à essayer dans l'ordre : [(mode, commande)]."""
        if self._build_cmd:
            return [("gpu", self._build_cmd(self.port))]
        cfg = self.config
        primary = locate_binary(cfg.llama_cpp_dir) if cfg.llama_cpp_dir else None
        cpu_exe = locate_binary(cfg.llama_cpp_cpu_dir) if cfg.llama_cpp_cpu_dir else None
        if primary is None and not (cfg.cpu_fallback and cpu_exe):
            find_binary(cfg.llama_cpp_dir)  # lève l'erreur de configuration explicite
        no_gpu = free_vram_mb() is None
        out: list[tuple[str, list[str]]] = []
        if primary is not None and not (no_gpu and cfg.cpu_fallback) and cfg.llama_ngl > 0:
            out.append(("gpu", self._llama_cmd(primary, cfg.llama_ngl)))
        if cfg.cpu_fallback or cfg.llama_ngl == 0:
            if primary is not None:
                out.append(("cpu", self._llama_cmd(primary, 0)))
            if cpu_exe is not None and cpu_exe != primary:
                out.append(("cpu", self._llama_cmd(cpu_exe, 0)))
        if not out:
            raise LlamaServerError("Aucun GPU NVIDIA détecté et le repli sur le processeur est désactivé "
                                   "(Administration > Moteur OCR).", permanent=True)
        return out

    def start(self) -> None:
        if self.running() and self.healthy():
            return
        self.stop()
        if self._build_cmd is None:
            kill_orphan(self.port)  # serveur laissé par un worker tué : il tiendrait le port et la VRAM
        if self.healthy():
            raise LlamaServerError(f"Le port {self.port} est déjà occupé par un autre serveur.")
        attempts = self._attempts()
        last: LlamaServerError | None = None
        for i, (mode, cmd) in enumerate(attempts):
            try:
                self._launch(cmd)
                self.mode = mode
                if mode == "cpu":
                    log.warning("llama-server tourne sur le PROCESSEUR (repli) : l'OCR sera lent")
                return
            except LlamaServerError as e:
                last = e
                if i + 1 < len(attempts):
                    log.warning("llama-server (%s) n'a pas démarré (%s) : solution de repli suivante", mode, e)
        assert last is not None
        raise last

    def _launch(self, cmd: list[str]) -> None:
        logdir = get_settings().data_dir / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        self._log_fp = open(logdir / f"{self.log_name}.log", "ab")  # journal technique : aucun contenu de document
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW) if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdout=self._log_fp, stderr=subprocess.STDOUT, creationflags=flags)
        kill_with_parent(self.proc)  # le serveur meurt avec le worker, même tué brutalement
        if self._build_cmd is None:
            try:
                f = _pid_file(self.port)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(str(self.proc.pid), encoding="ascii")
            except OSError:
                pass
        log.info("llama-server démarré (pid %s, port %s)", self.proc.pid, self.port)
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                code = self.proc.returncode
                self.proc = None
                self._forget_pid()
                self._close_log()
                raise LlamaServerError(f"llama-server s'est arrêté au démarrage (code {code}).")
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
        if p is not None:
            self._forget_pid()
        self._close_log()

    def _forget_pid(self) -> None:
        if self._build_cmd is None:
            try:
                _pid_file(self.port).unlink(missing_ok=True)
            except OSError:
                pass

    def _close_log(self) -> None:
        if self._log_fp:
            try:
                self._log_fp.close()
            finally:
                self._log_fp = None
