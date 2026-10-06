from app.core.db import session_scope
from app.models import AuditLog, User


def test_login_ok_et_me(client, make_user):
    make_user()
    r = client.post("/api/auth/login", json={"username": "Alice", "password": "Motdepasse123"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "alice" and body["access_token"]
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "ocr_refresh=" in cookie
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["role"] == "user"


def test_mauvais_mot_de_passe_message_generique(client, make_user):
    make_user()
    r1 = client.post("/api/auth/login", json={"username": "alice", "password": "faux"})
    r2 = client.post("/api/auth/login", json={"username": "inconnu", "password": "faux"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()  # n'indique pas si l'identifiant existe


def test_verrouillage_apres_echecs(client, make_user):
    make_user()
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "alice", "password": "faux"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "alice", "password": "Motdepasse123"})
    assert r.status_code == 429  # même avec le bon mot de passe
    with session_scope() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "login_failed").count() >= 5


def test_sans_jeton_401(client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/admin/users").status_code == 401


def test_changement_de_mot_de_passe_impose(client, make_user):
    make_user("bob", "Initial12345", must_change=True)
    r = client.post("/api/auth/login", json={"username": "bob", "password": "Initial12345"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/documents", headers=h).status_code == 403  # bloqué tant que non changé
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/change-password", headers=h,
                       json={"old_password": "Initial12345", "new_password": "court"}).status_code == 400
    ok = client.post("/api/auth/change-password", headers=h,
                     json={"old_password": "Initial12345", "new_password": "NouveauSecret987"})
    assert ok.status_code == 200
    h2 = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    assert client.get("/api/documents", headers=h2).status_code == 200
    assert client.get("/api/documents", headers=h).status_code == 401  # ancien jeton invalidé


def test_refresh_et_logout(client, make_user):
    make_user()
    client.post("/api/auth/login", json={"username": "alice", "password": "Motdepasse123"})
    assert client.post("/api/auth/refresh").status_code == 200  # cookie conservé par le client
    assert client.post("/api/auth/logout").status_code == 204
    assert client.post("/api/auth/refresh").status_code == 401


def test_compte_desactive(client, make_user, login):
    uid = make_user()
    h = login()
    with session_scope() as db:
        db.get(User, uid).is_active = False
    assert client.get("/api/documents", headers=h).status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "Motdepasse123"}).status_code == 401


def test_admin_requis(client, make_user, login):
    make_user("alice")
    make_user("root", role="admin")
    assert client.get("/api/admin/users", headers=login("alice")).status_code == 403
    assert client.get("/api/admin/users", headers=login("root")).status_code == 200
