"""Serveur d'interface (port dédié) : sert l'interface compilée et relaie /api vers l'API, sans rien casser."""
import io

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_pdf


@pytest.fixture()
def front(client, app_env):
    """Le serveur d'interface relié à l'API réelle (transport ASGI en mémoire), avec une interface factice."""
    from app.frontend import create_app
    from app.main import create_app as create_api

    dist = app_env / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>interface</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    api = create_api()
    with TestClient(create_app(upstream="http://api", transport=httpx.ASGITransport(app=api), dist=dist)) as c:
        yield c


def test_interface_servie_et_repli_sur_index(front):
    assert front.get("/").text == "<html>interface</html>"
    assert front.get("/lots/3").text == "<html>interface</html>"  # route de l'application : index.html
    assert front.get("/assets/app.js").text == "console.log(1)"
    assert front.get("/../../etc/passwd").text == "<html>interface</html>"  # jamais hors du dossier
    r = front.get("/")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]


def test_session_et_envoi_de_fichier_a_travers_le_relais(front, make_user):
    make_user()
    r = front.post("/api/auth/login", json={"username": "alice", "password": "Motdepasse123"})
    assert r.status_code == 200
    assert "ocr_refresh" in front.cookies or any("auth" in str(c) for c in front.cookies.jar)  # cookie de session relayé
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    up = front.post("/api/documents", headers=h, data={"preprocess": "auto", "llm": "off", "review": "false"},
                    files={"file": ("a.pdf", io.BytesIO(make_pdf(1)), "application/pdf")})
    assert up.status_code == 201 and up.json()["page_count"] == 1
    assert front.get("/api/documents?page_size=5", headers=h).json()["total"] == 1
    assert front.get("/api/documents/999", headers=h).status_code == 404  # codes d'erreur de l'API conservés
    assert front.post("/api/auth/refresh").status_code == 200  # le cookie relayé permet de renouveler la session


def test_api_arretee_ne_fait_pas_planter_l_interface():
    from app.frontend import create_app

    def refuse(request):
        raise httpx.ConnectError("refusé", request=request)

    with TestClient(create_app(upstream="http://api", transport=httpx.MockTransport(refuse))) as c:
        r = c.get("/api/status")
        assert r.status_code == 503 and "ne répond pas" in r.json()["detail"]
        assert c.get("/healthz").json() == {"status": "ok"}
