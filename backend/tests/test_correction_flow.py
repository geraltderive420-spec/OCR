"""Correction LLM de bout en bout (modes automatique / validation) et paramètres du modèle Ollama."""
import json

import httpx

from app.core.db import session_scope
from app.models import Block, Correction
from app.services import llm
from tests.conftest import make_pdf
from tests.test_documents import run_worker, upload
from tests.test_llm import FakeOllama

FIXES = {"Ceci est un paragraphe de démonstration, référence 2024/118 du 12/03/2024.":
         "Ceci est un paragraphe de démonstration, référence 2024/118 du 12/03/2024."}


def install_fake(monkeypatch, answers):
    f = FakeOllama(answers)
    monkeypatch.setattr(llm.httpx, "post", f.post)
    monkeypatch.setattr(llm.httpx, "get", f.get)
    return f


def test_mode_automatique_applique_et_annulable(client, make_user, login, monkeypatch):
    make_user()
    h = login()
    # Le modèle « corrige » le titre ET tente de changer un chiffre dans le paragraphe : seul le titre passe.
    f = install_fake(monkeypatch, {"Titre du documcnt": "Titre du document",
                                   "Ceci est un paragraphe de démonstration, référence 2024/118 du 12/03/2024.":
                                   "Ceci est un paragraphe de démonstration, référence 2024/181 du 12/03/2024."})
    doc = upload(client, h, make_pdf(1), llm="auto", llm_all=True, review=True).json()
    run_worker()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "review" and d["options"]["llm_stats"]["appliquees"] == 1
    page = client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()
    title = next(b for b in page["blocks"] if b["label"] == "SectionHeader")
    para = next(b for b in page["blocks"] if "paragraphe" in b["html"])
    assert title["html"] == "Titre du document" and para["html"].count("2024/118") == 1
    assert title["corrections"][0]["status"] == "auto_applied"
    # annulation de la correction automatique
    cid = title["corrections"][0]["id"]
    r = client.post(f"/api/corrections/{cid}/reject", headers=h)
    assert r.json()["html"] == "Titre du documcnt" and r.json()["corrections"][0]["status"] == "reverted"
    # le modèle de l'administrateur est bien celui utilisé, et il est déchargé ensuite
    chat = [r for r in f.requests if r[0].endswith("/api/chat")]
    assert chat and chat[0][1]["model"] == "gemma4:e2b"
    assert any(r[1] and r[1].get("keep_alive") == 0 and r[0].endswith("/api/generate") for r in f.requests)


def test_mode_validation_propose_puis_accepte(client, make_user, login, monkeypatch):
    make_user()
    h = login()
    install_fake(monkeypatch, {"Titre du documcnt": "Titre du document"})
    doc = upload(client, h, make_pdf(1), llm="validate", llm_all=True, review=True).json()
    run_worker()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["pending_corrections"] == 1
    page = client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()
    title = next(b for b in page["blocks"] if b["label"] == "SectionHeader")
    assert title["html"] == "Titre du documcnt"  # rien d'appliqué sans validation
    c = title["corrections"][0]
    assert c["status"] == "proposed" and c["after"] == "Titre du document"
    assert client.post(f"/api/corrections/{c['id']}/accept", headers=h).json()["html"] == "Titre du document"
    assert client.get(f"/api/documents/{doc['id']}", headers=h).json()["pending_corrections"] == 0


def test_mode_validation_refus(client, make_user, login, monkeypatch):
    make_user()
    h = login()
    install_fake(monkeypatch, {"Titre du documcnt": "Titre du document"})
    doc = upload(client, h, make_pdf(1), llm="validate", llm_all=True, review=True).json()
    run_worker()
    page = client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()
    c = next(b for b in page["blocks"] if b["label"] == "SectionHeader")["corrections"][0]
    r = client.post(f"/api/corrections/{c['id']}/reject", headers=h).json()
    assert r["html"] == "Titre du documcnt" and r["corrections"][0]["status"] == "rejected"


def test_seuil_de_confiance_par_defaut(client, make_user, login, monkeypatch):
    """Sans « tout corriger », seuls les blocs sous le seuil (0,85) sont envoyés au modèle."""
    make_user()
    h = login()
    f = install_fake(monkeypatch, {})
    upload(client, h, make_pdf(1), llm="auto")
    run_worker()
    sent = [json.loads(r[1]["messages"][1]["content"])["texte"] for r in f.requests if r[0].endswith("/api/chat")]
    assert any("العربية" in t for t in sent)  # confiance 0,62
    assert not any("Titre du documcnt" in t for t in sent)  # confiance 0,96


def test_ollama_arrete_application_utilisable(client, make_user, login, monkeypatch):
    make_user()
    h = login()

    def down(*a, **k):
        raise httpx.ConnectError("refusé")
    monkeypatch.setattr(llm.httpx, "get", down)
    monkeypatch.setattr(llm.httpx, "post", down)
    doc = upload(client, h, make_pdf(1), llm="auto").json()
    run_worker()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "done" and d["options"]["llm_stats"]["ignoree"] is True


def test_modele_ollama_par_document_et_parametre(client, make_user, login, monkeypatch):
    make_user("root", role="admin")
    h = login("root")
    f = install_fake(monkeypatch, {})
    assert client.put("/api/admin/settings", headers=h, json={"ollama_model": "qwen354:latest"}).json()["ollama_model"] == "qwen354:latest"
    upload(client, h, make_pdf(1), llm="auto", llm_all=True)
    run_worker()
    assert {r[1]["model"] for r in f.requests if r[0].endswith("/api/chat")} == {"qwen354:latest"}
    f.requests.clear()
    upload(client, h, make_pdf(1), llm="auto", llm_all=True, ollama_model="gemma4:e2b")  # choix propre au document
    run_worker()
    assert {r[1]["model"] for r in f.requests if r[0].endswith("/api/chat")} == {"gemma4:e2b"}


def test_parametres_refusent_les_modeles_cloud(client, make_user, login):
    make_user("root", role="admin")
    h = login("root")
    for bad in ("qwen3.5:cloud", "gemma4:31b-cloud", "kimi-k2.6:cloud", ""):
        assert client.put("/api/admin/settings", headers=h, json={"ollama_model": bad}).status_code == 422
    assert client.put("/api/admin/settings", headers=h, json={"llm_max_change_ratio": 7}).status_code == 422
    assert client.put("/api/admin/settings", headers=h, json={"inconnu": 1}).status_code == 422
    assert client.get("/api/admin/settings", headers=h).json()["ollama_model"] == "gemma4:e2b"


def test_liste_des_modeles_exclut_les_cloud(client, make_user, login, monkeypatch):
    from app.api import admin

    make_user("root", role="admin")
    monkeypatch.setattr(admin, "ollama_tags", lambda: [
        {"name": "gemma4:e2b", "size": 7_200_000_000}, {"name": "qwen354:latest", "size": 3_100_000_000},
        {"name": "qwen3.5:cloud"}, {"name": "gemma4:31b-cloud"}, {"name": "autre:latest", "remote_host": "https://x"}])
    out = client.get("/api/admin/ollama/models", headers=login("root")).json()
    assert out["available"] and [m["name"] for m in out["models"]] == ["gemma4:e2b", "qwen354:latest"]
    monkeypatch.setattr(admin, "ollama_tags", lambda: None)
    assert client.get("/api/admin/ollama/models", headers=login("root")).json()["available"] is False


def test_options_pour_tout_utilisateur(client, make_user, login, monkeypatch):
    from app.api import admin

    make_user()
    monkeypatch.setattr(admin, "ollama_tags", lambda: [{"name": "gemma4:e2b"}, {"name": "x:cloud"}])
    out = client.get("/api/options", headers=login()).json()
    assert out["ollama_models"] == ["gemma4:e2b"] and out["default_model"] == "gemma4:e2b"
    assert client.get("/api/options").status_code == 401
