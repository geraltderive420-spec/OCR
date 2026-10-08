"""Lancement de l'application en une commande : python -m app.serve

1. met la base au niveau du code (copie de sécurité avant migration) ;
2. démarre l'API (uvicorn) et le worker OCR comme deux processus enfants ;
3. relance un enfant qui s'arrête de façon inattendue (délai croissant, plafonné à une minute) ;
4. Ctrl+C (ou fermeture) : arrêt propre du worker, qui arrête lui-même llama-server et libère la VRAM.

Journaux : <data_dir>/logs/api.log et worker.log (rotation à 10 Mo). Aucun contenu de document n'y est écrit.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import BACKEND_DIR, DEFAULT_SECRET, get_settings
from app.core.winjob import kill_with_parent

log = logging.getLogger("ocr.serve")
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
MAX_LOG = 10 * 1024 * 1024


class Child:
    def __init__(self, name: str, args: list[str], logfile: Path):
        self.name, self.args, self.logfile = name, args, logfile
        self.proc: subprocess.Popen | None = None
        self.fails, self.next_start = 0, 0.0
        self.started_at = 0.0

    def start(self) -> None:
        self._rotate()
        fp = open(self.logfile, "ab")
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen([sys.executable, *self.args], cwd=str(BACKEND_DIR), stdout=fp,
                                     stderr=subprocess.STDOUT, env=env, creationflags=NEW_GROUP)
        fp.close()  # le processus enfant garde sa propre copie du descripteur
        kill_with_parent(self.proc)  # fenêtre fermée ou superviseur tué : les enfants (et llama-server) suivent
        self.started_at = time.monotonic()
        log.info("%s démarré (pid %s)", self.name, self.proc.pid)

    def _rotate(self) -> None:
        try:
            if self.logfile.exists() and self.logfile.stat().st_size > MAX_LOG:
                old = self.logfile.with_suffix(".1.log")
                old.unlink(missing_ok=True)
                self.logfile.replace(old)
        except OSError:
            pass

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 30.0) -> None:
        p = self.proc
        if p is None or p.poll() is not None:
            return
        try:
            if os.name == "nt":
                p.send_signal(signal.CTRL_BREAK_EVENT)  # le worker arrête llama-server avant de sortir
            else:
                p.terminate()
            p.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            p.kill()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        log.info("%s arrêté", self.name)


def _setup_logging(logdir: Path) -> None:
    logdir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    h = RotatingFileHandler(logdir / "serve.log", maxBytes=MAX_LOG, backupCount=2, encoding="utf-8")
    h.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(h)
    root.addHandler(console)


def _uvicorn_args() -> list[str]:
    s = get_settings()
    args = ["-m", "uvicorn", "app.main:app", "--host", s.api_host, "--port", str(s.api_port), "--no-server-header",
            "--proxy-headers"]
    if s.ssl_certfile and s.ssl_keyfile:
        if Path(s.ssl_certfile).is_file() and Path(s.ssl_keyfile).is_file():
            args += ["--ssl-certfile", str(s.ssl_certfile), "--ssl-keyfile", str(s.ssl_keyfile)]
        else:
            log.warning("certificat HTTPS introuvable : l'API démarre en HTTP")
    return args


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1" if host in ("0.0.0.0", "") else host, port)) != 0


def main() -> int:
    s = get_settings()
    _setup_logging(s.data_dir / "logs")
    if s.secret_key == DEFAULT_SECRET:
        log.warning("OCR_SECRET_KEY par défaut : à changer avant toute mise en production.")

    try:
        from app import maintenance

        log.info(maintenance.migrate(backup=True))
    except Exception:  # noqa: BLE001 - sans base au bon niveau, rien ne fonctionnerait : on s'arrête net
        log.exception("migration de la base impossible : arrêt (rien n'a été modifié sans copie de sécurité)")
        return 2

    if not _port_free(s.api_host, s.api_port):
        log.error("le port %s est déjà utilisé : l'application tourne-t-elle déjà ? (sinon changez OCR_API_PORT)",
                  s.api_port)
        return 4
    front_host = s.frontend_host or s.api_host
    if s.frontend_port and not _port_free(front_host, s.frontend_port):
        log.error("le port %s de l'interface est déjà utilisé (sinon changez OCR_FRONTEND_PORT)", s.frontend_port)
        return 4

    logs = s.data_dir / "logs"
    children = [Child("api", _uvicorn_args(), logs / "api.log"),
                Child("worker", ["-m", "app.worker.runner"], logs / "worker.log")]
    if s.frontend_port:
        children.insert(1, Child("interface", ["-m", "app.frontend"], logs / "frontend.log"))
    stopping = False

    def _stop(*_a) -> None:
        nonlocal stopping
        stopping = True

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

    scheme = "https" if s.ssl_certfile and s.ssl_keyfile else "http"
    for c in children:
        c.start()
    shown = s.frontend_port or s.api_port
    log.info("application disponible sur %s://%s:%s  (API : %s ; Ctrl+C pour arrêter)", scheme,
             "127.0.0.1" if front_host in ("0.0.0.0", "") else front_host, shown, s.api_port)
    try:
        while not stopping:
            time.sleep(1.0)
            now = time.monotonic()
            for c in children:
                if c.alive() or stopping:
                    continue
                if c.proc is not None and c.next_start == 0.0:
                    code = c.proc.returncode
                    if c.name == "worker" and code == 3:
                        log.error("un autre worker tourne déjà sur ce dossier de données : pas de relance")
                        c.proc, c.next_start = None, float("inf")
                        continue
                    c.fails = c.fails + 1 if now - c.started_at < 120 else 1
                    delay = min(60.0, 2.0 ** c.fails)
                    log.warning("%s s'est arrêté (code %s) : relance dans %.0f s (voir %s)", c.name, code, delay,
                                c.logfile.name)
                    c.next_start = now + delay
                if now >= c.next_start:
                    c.next_start = 0.0
                    c.start()
    except KeyboardInterrupt:
        pass
    log.info("arrêt de l'application...")
    for c in reversed(children):  # worker d'abord : il libère le GPU
        c.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
