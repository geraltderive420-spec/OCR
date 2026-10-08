"""Réglages du moteur OCR, solutions de repli, sauvegarde SQLite, superviseur."""
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests.conftest import make_pdf
from tests.test_documents import run_worker, upload


def admin(client, make_user, login):
    make_user("root", role="admin")
    return login("root")


def test_reglages_moteur_ocr_en_base_par_dessus_env(client, make_user, login, monkeypatch, app_env):
    from app.core import config
    from app.core.db import session_scope
    from app.services.ocr_config import OcrConfig

    monkeypatch.setenv("OCR_LLAMA_CPP_DIR", "C:/depuis/env")
    config.reset_settings_cache()
    h = admin(client, make_user, login)
    ocr = client.get("/api/admin/ocr", headers=h).json()
    assert Path(ocr["settings"]["llama_cpp_dir"]) == Path("C:/depuis/env") and ocr["defaults"]["page_width"] == 0
    assert ocr["binary"]["ok"] is False
    r = client.put("/api/admin/settings", headers=h,
                   json={"llama_cpp_dir": ' "D:\\llama\\bin\\" ', "page_width": 2200, "llama_parallel": 2})
    assert r.status_code == 200 and r.json()["llama_cpp_dir"] == "D:\\llama\\bin"  # guillemets et \ final retirés
    with session_scope() as db:
        cfg = OcrConfig.load(db)
    assert cfg.page_width == 2200 and cfg.llama_parallel == 2 and str(cfg.llama_cpp_dir).startswith("D:")
    assert client.put("/api/admin/settings", headers=h, json={"page_width": 500}).status_code == 422
    assert client.put("/api/admin/settings", headers=h, json={"line_detector_device": "tpu"}).status_code == 422
    r = client.post("/api/admin/settings/reset", headers=h, json={"keys": ["llama_cpp_dir", "page_width"]})
    assert Path(r.json()["llama_cpp_dir"]) == Path("C:/depuis/env") and r.json()["page_width"] == 0


def test_largeur_des_pages_donne_la_resolution(client, make_user, login):
    from app.services.pipeline import render_dpi

    assert render_dpi(595, 0) == 200  # A4, automatique
    assert render_dpi(595, 2500) == 303
    assert render_dpi(595, 9000) == 400 and render_dpi(595, 100) == 100  # bornes
    h = admin(client, make_user, login)
    client.put("/api/admin/settings", headers=h, json={"page_width": 1500})
    doc = upload(client, h, make_pdf(1, size=(1240, 1754))).json()  # 1240 px à 150 dpi = 595 pt
    run_worker()
    page = client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()
    assert abs(page["width"] - 1500) <= 10


def test_verification_du_chemin_llama(client, make_user, login, app_env):
    h = admin(client, make_user, login)
    r = client.post("/api/admin/ocr/check-llama", headers=h, json={"path": str(app_env / "rien")}).json()
    assert r["configured"] and not r["ok"] and r["path"] is None
    racine = app_env / "llama.cpp"
    (racine / "build" / "bin" / "Release").mkdir(parents=True)
    exe = racine / "build" / "bin" / "Release" / ("llama-server.exe" if os.name == "nt" else "llama-server")
    exe.write_bytes(b"pas un vrai binaire")
    r = client.post("/api/admin/ocr/check-llama", headers=h, json={"path": str(racine)}).json()
    assert r["path"] == str(exe) and not r["ok"]  # trouvé depuis la racine, mais ne démarre pas


def test_ordre_des_solutions_de_repli(app_env, monkeypatch):
    from app.services import gpu
    from app.services.ocr_config import OcrConfig

    def fake(dirname):
        d = app_env / dirname
        d.mkdir()
        (d / gpu.EXE).write_bytes(b"x")
        return d

    cuda, cpu = fake("cuda"), fake("cpu")
    cfg = OcrConfig(llama_cpp_dir=cuda, llama_cpp_cpu_dir=cpu, cpu_fallback=True)
    srv = gpu.LlamaServer(config=cfg, model_files=lambda: ("m.gguf", "p.gguf"))
    monkeypatch.setattr(gpu, "free_vram_mb", lambda: 8000)
    modes = [(m, c[0], c[c.index("-ngl") + 1]) for m, c in srv._attempts()]
    assert modes == [("gpu", str(cuda / gpu.EXE), "99"), ("cpu", str(cuda / gpu.EXE), "0"), ("cpu", str(cpu / gpu.EXE), "0")]
    monkeypatch.setattr(gpu, "free_vram_mb", lambda: None)  # aucun GPU : directement le processeur
    assert [m for m, _ in srv._attempts()] == ["cpu", "cpu"]
    # Sans repli : nvidia-smi absent ne prouve pas l'absence de GPU, le build CUDA est tout de même essayé.
    srv = gpu.LlamaServer(config=OcrConfig(llama_cpp_dir=cuda, cpu_fallback=False), model_files=lambda: ("m", "p"))
    assert [m for m, _ in srv._attempts()] == ["gpu"]
    srv = gpu.LlamaServer(config=OcrConfig(llama_cpp_dir=app_env / "absent", cpu_fallback=False))
    with pytest.raises(gpu.LlamaServerError, match="introuvable"):
        srv._attempts()


def test_repli_quand_le_serveur_gpu_plante(app_env, monkeypatch):
    """Premier lancement en échec (DLL CUDA absente...) : le lancement suivant de la liste est essayé."""
    from app.services import gpu

    srv = gpu.LlamaServer(port=1, startup_timeout=5)
    tried = []

    def launch(cmd):
        tried.append(cmd[0])
        if cmd[0] == "gpu-exe":
            raise gpu.LlamaServerError("s'est arrêté au démarrage (code -1073741515)")

    monkeypatch.setattr(srv, "healthy", lambda: False)
    monkeypatch.setattr(srv, "_attempts", lambda: [("gpu", ["gpu-exe"]), ("cpu", ["cpu-exe"])])
    monkeypatch.setattr(srv, "_launch", launch)
    srv.start()
    assert tried == ["gpu-exe", "cpu-exe"] and srv.mode == "cpu"


def test_moteur_redemarre_si_les_reglages_changent(client, make_user, login):
    from app.core.db import session_scope
    from app.services import app_settings
    from app.worker.runner import Worker

    starts = []

    class Eng:
        name = "fake"

        def start(self):
            starts.append(1)

        def stop(self):
            pass

    w = Worker(engine_factory=Eng)
    with session_scope() as db:
        w.acquire_engine(db)
        w.acquire_engine(db)
        assert len(starts) == 1
        app_settings.set_setting(db, "page_width", 2000)  # lu à chaque page : pas de redémarrage
        db.commit()
        w.acquire_engine(db)
        assert len(starts) == 1
        app_settings.set_setting(db, "llama_parallel", 3)  # lu au démarrage du moteur : redémarrage
        db.commit()
        w.acquire_engine(db)
        assert len(starts) == 2


def test_orphelin_jamais_tue_s_il_n_est_pas_llama_server(app_env):
    from app.services import gpu

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        f = gpu._pid_file(9999)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(str(p.pid))
        assert gpu.kill_orphan(9999) is False  # c'est python.exe : on n'y touche pas
        assert p.poll() is None and not f.exists()
    finally:
        p.kill()


@pytest.mark.skipif(os.name != "nt", reason="Job Object Windows")
def test_enfant_lie_a_la_vie_du_parent():
    from app.core.winjob import kill_with_parent

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert kill_with_parent(p) is True
    finally:
        p.kill()


def test_un_seul_worker_a_la_fois(app_env):
    from app.worker.runner import SingleInstance

    lock = app_env / "run" / "worker.lock"
    a, b = SingleInstance(lock), SingleInstance(lock)
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_battement_de_coeur_et_statut(client, make_user, login):
    from app.worker.runner import Worker, read_status

    make_user()
    h = login()
    assert client.get("/api/status", headers=h).json()["worker_alive"] is False
    w = Worker()
    w.heartbeat(force=True)
    st = client.get("/api/status", headers=h).json()
    assert st["worker_alive"] is True and st["queue"] == 0
    w.state = "stopped"
    w.heartbeat(force=True)
    assert read_status()["alive"] is False


def test_sauvegarde_et_restauration_sqlite(client, make_user, login, app_env):
    from app import maintenance
    from app.core.db import get_engine

    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1)).json()
    run_worker()
    archive = maintenance.backup(app_env / "sauvegardes")
    names = zipfile.ZipFile(archive).namelist()
    assert "base.sqlite3" in names and any(n.startswith("exports/") for n in names)
    client.delete(f"/api/documents/{doc['id']}", headers=h)
    assert client.get(f"/api/documents/{doc['id']}", headers=h).status_code == 404
    get_engine().dispose()  # application « arrêtée »
    maintenance.restore(archive)
    get_engine().dispose()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "done" and client.get(f"/api/documents/{doc['id']}/export.docx", headers=h).status_code == 200
    assert list((app_env / "data" / "backups").glob("avant-restauration-*.sqlite3"))


def test_migration_d_une_base_neuve(tmp_path, monkeypatch):
    from app import maintenance
    from app.core import config, db

    monkeypatch.setenv("OCR_DATA_DIR", str(tmp_path / "donnees"))
    monkeypatch.delenv("OCR_DATABASE_URL", raising=False)
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    config.reset_settings_cache()
    db.init_engine()
    try:
        assert "migrée" in maintenance.migrate()
        assert maintenance.migrate().startswith("base à jour")
        assert (tmp_path / "donnees" / "db" / "ocr.sqlite3").is_file()
        with db.get_engine().connect() as c:
            assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
    finally:
        db.get_engine().dispose()
        config.reset_settings_cache()


def test_image_illisible_refusee(client, make_user, login):
    import io

    make_user()
    h = login()
    r = client.post("/api/documents", headers=h, files={"file": ("x.png", io.BytesIO(b"\x89PNG\r\n\x1a\nabimee"), "image/png")})
    assert r.status_code == 422
