from datetime import timedelta

import cv2
import numpy as np
import pytest

from app.core.db import session_scope
from app.models import AuditLog, Document, User, utcnow
from app.services import preprocess, storage
from app.services.stamps import detect_stamps
from tests.conftest import make_pdf
from tests.test_documents import run_worker, upload


# ---------------------------------------------------------------- prétraitement

def text_page(h=1000, w=760, ink=0, paper=255, angle=0.0):
    img = np.full((h, w), paper, np.uint8)
    for y in range(120, h - 100, 45):
        cv2.putText(img, "ligne de texte de test 123", (60, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, int(ink), 2, cv2.LINE_AA)
    return preprocess.rotate(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), angle) if angle else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_redressement():
    page = text_page(angle=2.5)
    est = preprocess.estimate_skew(cv2.cvtColor(page, cv2.COLOR_BGR2GRAY))
    assert abs(est + 2.5) <= 0.5  # l'angle à appliquer annule l'inclinaison
    straight, *_ = preprocess.process_page(page, "leger")
    assert abs(preprocess.estimate_skew(cv2.cvtColor(straight, cv2.COLOR_BGR2GRAY))) <= 0.5


def test_profil_auto_selon_le_contraste():
    sharp = cv2.cvtColor(text_page(), cv2.COLOR_BGR2GRAY)
    faded = cv2.cvtColor(text_page(ink=170, paper=225), cv2.COLOR_BGR2GRAY)
    assert preprocess.choose_profile(sharp) == "leger"
    assert preprocess.choose_profile(faded) in ("delave", "tres_delave")
    assert preprocess.measure_contrast(sharp) > preprocess.measure_contrast(faded)


def test_normalisation_du_fond_et_contraste():
    faded = text_page(ink=150, paper=215)
    yy, xx = np.mgrid[0:faded.shape[0], 0:faded.shape[1]]
    shade = (1.0 - 0.35 * xx / faded.shape[1])[..., None]  # éclairage inégal
    faded = (faded * shade).astype(np.uint8)
    _, out, used, _ = preprocess.process_page(faded, "delave")
    g = out[..., 0]
    assert used == "delave" and out.shape == faded.shape
    left, right = np.median(g[:, :100]), np.median(g[:, -100:])
    assert abs(float(left) - float(right)) < 25  # le fond est uniforme
    raw = cv2.cvtColor(faded, cv2.COLOR_BGR2GRAY)
    gap_before = float(np.median(raw)) - float(np.percentile(raw, 1))
    gap_after = float(np.median(g)) - float(np.percentile(g, 1))
    assert gap_after > 1.5 * gap_before  # l'encre ressort nettement mieux du fond


def test_profil_inconnu():
    with pytest.raises(ValueError):
        preprocess.process_page(text_page(), "bidon")


# ---------------------------------------------------------------- tampons

def stamp_page():
    img = np.full((1000, 800, 3), 255, np.uint8)
    cv2.putText(img, "texte noir", (60, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    cv2.circle(img, (600, 700), 90, (200, 60, 30), 6)  # BGR : tampon bleu
    cv2.circle(img, (600, 700), 65, (200, 60, 30), 3)
    cv2.putText(img, "VU", (570, 710), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (200, 60, 30), 3)
    return img


def test_detection_tampon_bleu():
    found = detect_stamps(stamp_page(), visual_boxes=[], text_boxes=[(50, 150, 400, 220)])
    assert len(found) == 1 and found[0].color == "bleu" and not found[0].overlaps_text
    x0, y0, x1, y1 = found[0].bbox
    assert x0 < 520 and x1 > 680 and y0 < 620 and y1 > 780


def test_tampon_deja_detecte_ou_recouvrant_du_texte():
    assert detect_stamps(stamp_page(), visual_boxes=[(500, 600, 700, 800)], text_boxes=[]) == []
    found = detect_stamps(stamp_page(), visual_boxes=[], text_boxes=[(480, 600, 720, 800)])
    assert found[0].overlaps_text  # bloc « à vérifier »


def test_page_noire_et_blanche_sans_tampon():
    assert detect_stamps(np.full((500, 500, 3), 255, np.uint8), [], []) == []
    assert detect_stamps(np.zeros((500, 500, 3), np.uint8), [], []) == []


# ---------------------------------------------------------------- stockage

def test_stockage_refuse_la_sortie_du_dossier(app_env):
    with pytest.raises(storage.StorageError):
        storage.resolve("../../etc/passwd")
    with pytest.raises(storage.StorageError):
        storage.resolve("uploads/../../secret")
    p, rel = storage.new_path("uploads", 1, 2, ".pdf")
    assert storage.resolve(rel) == p.resolve() and len(p.stem) == 32


# ---------------------------------------------------------------- rétention et administration

def test_purge_de_retention(client, make_user, login):
    make_user()
    h = login()
    old = upload(client, h, make_pdf(1)).json()
    new = upload(client, h, make_pdf(1)).json()
    run_worker()
    with session_scope() as db:
        db.get(Document, old["id"]).created_at = utcnow() - timedelta(days=45)
    from app.services.retention import purge_expired

    with session_scope() as db:
        assert purge_expired(db) == 1
    assert client.get(f"/api/documents/{old['id']}", headers=h).status_code == 404
    assert client.get(f"/api/documents/{new['id']}", headers=h).status_code == 200
    with session_scope() as db:
        assert db.query(AuditLog).filter_by(action="purge").count() == 1


def test_purge_ignore_les_documents_en_cours(client, make_user, login):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1)).json()  # en file : pas encore traité
    with session_scope() as db:
        db.get(Document, doc["id"]).created_at = utcnow() - timedelta(days=400)
        from app.services.retention import purge_expired

        assert purge_expired(db) == 0


def test_administration_utilisateurs(client, make_user, login):
    make_user("root", role="admin")
    h = login("root")
    r = client.post("/api/admin/users", headers=h, json={"username": "Nouveau.User", "password": "Provisoire12345"})
    assert r.status_code == 201 and r.json()["username"] == "nouveau.user" and r.json()["must_change_password"]
    assert client.post("/api/admin/users", headers=h, json={"username": "nouveau.user", "password": "Provisoire12345"}).status_code == 409
    assert client.post("/api/admin/users", headers=h, json={"username": "x y", "password": "Provisoire12345"}).status_code == 422
    assert client.post("/api/admin/users", headers=h, json={"username": "faible", "password": "abc"}).status_code == 422
    uid = r.json()["id"]
    # le nouvel utilisateur se connecte, puis est désactivé : sa session tombe
    nh = login("nouveau.user", "Provisoire12345")
    assert client.patch(f"/api/admin/users/{uid}", headers=h, json={"is_active": False}).json()["is_active"] is False
    assert client.get("/api/auth/me", headers=nh).status_code == 401
    # on ne peut pas se retirer ses propres droits
    me = client.get("/api/auth/me", headers=h).json()["id"]
    assert client.patch(f"/api/admin/users/{me}", headers=h, json={"is_active": False}).status_code == 409
    assert client.patch(f"/api/admin/users/{me}", headers=h, json={"role": "user"}).status_code == 409
    # réinitialisation de mot de passe : changement imposé
    assert client.post(f"/api/admin/users/{uid}/reset-password", headers=h, json={"password": "AutreSecret999"}).status_code == 200
    with session_scope() as db:
        assert db.get(User, uid).must_change_password


def test_admin_voit_les_metadonnees_pas_le_contenu(client, make_user, login):
    make_user("alice")
    make_user("root", role="admin")
    ha, hr = login("alice"), login("root")
    doc = upload(client, ha, make_pdf(1), review=True).json()
    run_worker()
    stats = client.get("/api/admin/stats", headers=hr).json()
    assert stats["documents"] == 1 and stats["pages_total"] == 1
    users = client.get("/api/admin/users", headers=hr).json()
    assert next(u for u in users if u["username"] == "alice")["documents"] == 1
    # l'administrateur n'a aucun accès au contenu d'un document d'autrui
    assert client.get(f"/api/documents/{doc['id']}", headers=hr).status_code == 404
    assert client.get(f"/api/documents/{doc['id']}/export.docx", headers=hr).status_code == 404
    assert "alice" not in str(client.get("/api/admin/services", headers=hr).json())


def test_cli_creation_admin(app_env, monkeypatch):
    from app import cli

    monkeypatch.setenv("OCR_ADMIN_PASSWORD", "MotDePasseAdmin42")
    assert cli.main(["create-admin", "Chef"]) == 0
    assert cli.main(["create-admin", "chef"]) == 1  # existe déjà
    with session_scope() as db:
        u = db.query(User).filter_by(username="chef").one()
        assert u.role == "admin" and u.must_change_password
    monkeypatch.setenv("OCR_ADMIN_PASSWORD", "court")
    assert cli.main(["create-admin", "autre"]) == 2


def test_en_tetes_de_securite_et_pas_de_trace_en_cas_d_erreur(client, make_user, login, monkeypatch):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    for path in ("/docs", "/openapi.json", "/redoc"):  # pas de documentation de l'API exposée
        r = client.get(path)
        assert "openapi" not in r.text.lower() and "application/json" not in r.headers.get("content-type", "")


def test_le_test_reseau_detecte_un_appel_sortant():
    import socket

    s = socket.socket()
    with pytest.raises(AssertionError, match="interdit"):
        s.connect(("93.184.216.34", 80))
    s.close()


def test_autodiagnostic(app_env, monkeypatch, capsys):
    from app import cli
    from app.core import config
    from app.selfcheck import KO, OK, run_checks

    monkeypatch.setenv("OCR_SECRET_KEY", config.DEFAULT_SECRET)
    config.reset_settings_cache()
    res = run_checks()
    assert any(r.level == KO and "OCR_SECRET_KEY" in r.message for r in res)  # clé par défaut refusée
    assert any(r.level == KO and "Migrations" in r.message for r in res)  # base de test créée sans Alembic
    assert any(r.level == OK and "Base de données joignable" in r.message for r in res)
    assert cli.main(["check"]) == 1
    out = capsys.readouterr().out
    assert config.DEFAULT_SECRET not in out and "échec" in out  # jamais de secret affiché
    monkeypatch.setenv("OCR_SECRET_KEY", "une-autre-cle-aleatoire-de-plus-de-32-caracteres")
    config.reset_settings_cache()
    assert not any("OCR_SECRET_KEY" in r.message and r.level == KO for r in run_checks())


def test_compte_de_developpement(app_env, client, monkeypatch):
    from app import cli
    from app.dev_seed import DEV_PASSWORD, DEV_USERNAME

    assert cli.main(["seed-dev"]) == 0
    r = client.post("/api/auth/login", json={"username": DEV_USERNAME, "password": DEV_PASSWORD})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin" and not r.json()["user"]["must_change_password"]
    assert cli.main(["seed-dev"]) == 0  # idempotent
    from app.core import config

    monkeypatch.setenv("OCR_COOKIE_SECURE", "true")  # production : refusé
    config.reset_settings_cache()
    assert cli.main(["seed-dev"]) == 3


def test_profil_auto_redresse_sans_alterer_l_image():
    page = text_page()
    color, for_ocr, used, _ = preprocess.process_page(page, "auto")
    assert used == "aucun"
    assert (cv2.cvtColor(for_ocr, cv2.COLOR_RGB2BGR) == color).all()


def test_budget_de_tokens_des_blocs():
    from app.services.ocr.surya2 import token_budget
    assert token_budget(50) == 350 and token_budget(1000) == 2000
