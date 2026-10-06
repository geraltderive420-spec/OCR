import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OCR_SECRET_KEY", "cle-de-test-suffisamment-longue-pour-hs256")


def _dotenv(key: str) -> str | None:
    f = Path(__file__).resolve().parents[1] / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return None


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Base SQLite et dossier de données isolés pour chaque test."""
    # OCR_TEST_DATABASE_URL (MariaDB de test) : mêmes tests sur la vraie base ; sinon SQLite isolé.
    test_url = os.environ.get("OCR_TEST_DATABASE_URL") or _dotenv("OCR_TEST_DATABASE_URL")
    use_maria = bool(test_url) and os.environ.get("OCR_TEST_SQLITE") != "1"
    monkeypatch.setenv("OCR_DATABASE_URL", test_url if use_maria else f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("OCR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OCR_OCR_BACKEND", "dummy")
    from app.core import config, db

    config.reset_settings_cache()
    engine = db.init_engine()
    import app.models  # noqa: F401

    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    yield tmp_path
    db.Base.metadata.drop_all(engine)
    engine.dispose()
    config.reset_settings_cache()
    shutil.rmtree(tmp_path / "data", ignore_errors=True)


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def make_user(app_env):
    from app.core.db import session_scope
    from app.core.security import hash_password
    from app.models import User

    def _make(username="alice", password="Motdepasse123", role="user", must_change=False):
        with session_scope() as db:
            u = User(username=username, password_hash=hash_password(password), role=role, must_change_password=must_change)
            db.add(u)
            db.flush()
            return u.id
    return _make


@pytest.fixture()
def login(client):
    def _login(username="alice", password="Motdepasse123"):
        r = client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _login


def make_pdf(pages=2, size=(1240, 1754)):
    """PDF d'images (pages blanches avec du texte dessiné) : suffisant pour le rendu, le moteur factice fait le reste."""
    import io

    import cv2
    import numpy as np
    from PIL import Image

    imgs = []
    for i in range(pages):
        a = np.full((size[1], size[0], 3), 255, np.uint8)
        cv2.putText(a, f"Page {i + 1}", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 5)
        imgs.append(Image.fromarray(a))
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:], resolution=150)
    return buf.getvalue()
