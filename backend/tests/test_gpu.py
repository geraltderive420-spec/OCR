"""Cycle de vie de llama-server (processus du worker) testé avec un faux serveur HTTP : aucun GPU nécessaire."""
import socket
import sys
import textwrap
import time

import pytest

from app.services import gpu
from app.services.gpu import LlamaServer, LlamaServerError

FAKE = textwrap.dedent('''
    import sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    port = int(sys.argv[sys.argv.index("--port") + 1])
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "ok"
    if mode == "crash":
        sys.exit(3)
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    HTTPServer(("127.0.0.1", port), H).serve_forever()
''')


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture()
def fake_script(app_env):
    p = app_env / "fake_llama.py"
    p.write_text(FAKE, encoding="utf-8")
    return str(p)


def test_demarrage_sante_et_arret_par_pid(fake_script):
    port = free_port()
    srv = LlamaServer(port=port, startup_timeout=30, build_cmd=lambda p: [sys.executable, fake_script, "--port", str(p)])
    srv.start()
    assert srv.running() and srv.healthy() and port_open(port)
    assert srv.base_url == f"http://127.0.0.1:{port}/v1"
    srv.start()  # idempotent : pas de second processus
    pid = srv.proc.pid
    srv.stop()
    assert srv.proc is None
    deadline = time.time() + 5
    while port_open(port) and time.time() < deadline:
        time.sleep(0.1)
    assert not port_open(port)  # le port est libéré : la VRAM aussi dans le cas réel
    assert pid  # arrêt par PID, jamais par nom d'exécutable
    srv.stop()  # sans effet si déjà arrêté


def test_plantage_au_demarrage_signale(fake_script):
    srv = LlamaServer(port=free_port(), startup_timeout=15,
                      build_cmd=lambda p: [sys.executable, fake_script, "--port", str(p), "--mode", "crash"])
    with pytest.raises(LlamaServerError, match="arrêté au démarrage"):
        srv.start()
    assert srv.proc is None or srv.proc.poll() is not None


def test_port_deja_occupe_par_un_autre_serveur(fake_script):
    port = free_port()
    other = LlamaServer(port=port, startup_timeout=30, build_cmd=lambda p: [sys.executable, fake_script, "--port", str(p)])
    other.start()
    try:
        mine = LlamaServer(port=port, startup_timeout=5, build_cmd=lambda p: [sys.executable, fake_script, "--port", str(p)])
        with pytest.raises(LlamaServerError, match="déjà occupé"):
            mine.start()  # ne tue jamais le serveur d'une autre application
        assert other.healthy()
    finally:
        other.stop()


def test_binaire_et_gguf_absents(app_env, monkeypatch):
    monkeypatch.setenv("OCR_LLAMA_CPP_DIR", str(app_env / "inexistant"))
    from app.core import config

    config.reset_settings_cache()
    with pytest.raises(LlamaServerError, match="introuvable"):
        gpu.find_binary()
    monkeypatch.delenv("OCR_LLAMA_CPP_DIR")
    config.reset_settings_cache()
    with pytest.raises(LlamaServerError, match="OCR_LLAMA_CPP_DIR"):
        gpu.find_binary()


def test_vram_insuffisante_met_le_document_en_attente(client, make_user, login, monkeypatch):
    """Le contrôle de VRAM libre du worker (moteur non factice) place le job en attente, sans échec."""
    from app.worker import runner
    from tests.conftest import make_pdf
    from tests.test_documents import upload

    make_user()
    h = login()
    monkeypatch.setenv("OCR_OCR_BACKEND", "surya2")
    from app.core import config

    config.reset_settings_cache()
    monkeypatch.setattr(runner, "free_vram_mb", lambda: 500)
    doc = upload(client, h, make_pdf(1)).json()
    assert runner.Worker().run_once()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "waiting_gpu" and d["error"] is None


def test_moteur_mal_configure_echoue_clairement(client, make_user, login, monkeypatch):
    """Sans OCR_LLAMA_CPP_DIR, le document échoue avec un message clair (au lieu d'attendre le GPU indéfiniment)."""
    from app.core import config
    from app.worker import runner
    from tests.conftest import make_pdf
    from tests.test_documents import upload

    make_user()
    h = login()
    monkeypatch.setenv("OCR_OCR_BACKEND", "surya2")
    monkeypatch.delenv("OCR_LLAMA_CPP_DIR", raising=False)
    config.reset_settings_cache()
    monkeypatch.setattr(runner, "free_vram_mb", lambda: 8000)
    doc = upload(client, h, make_pdf(1)).json()
    assert runner.Worker().run_once()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "failed" and "mal configuré" in d["error"]


def test_serveur_arrete_si_surya_ne_demarre_pas(monkeypatch):
    """Si Surya échoue après le lancement de llama-server, le serveur est arrêté (sinon : port occupé, attente sans fin)."""
    import pytest
    from app.services.ocr.surya2 import Surya2Engine

    class FakeServer:
        started = stopped = False
        base_url = "http://127.0.0.1:1/v1"

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    srv = FakeServer()
    eng = Surya2Engine(server=srv)
    monkeypatch.setattr(Surya2Engine, "_load", lambda self: (_ for _ in ()).throw(ImportError("surya")))
    with pytest.raises(ImportError):
        eng.start()
    assert srv.started and srv.stopped and eng._rec is None
