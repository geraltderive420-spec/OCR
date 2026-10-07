import io
from datetime import timedelta

from app.core.config import get_settings
from app.core.db import session_scope
from app.models import AuditLog, Block, Correction, Document, Job, JobStatus, utcnow
from app.worker.runner import Worker
from tests.conftest import make_pdf


def upload(client, h, pdf=None, name="Mon scan.pdf", **form):
    data = {"preprocess": "auto", "llm": "off", "review": "false",
            **{k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()}}
    return client.post("/api/documents", headers=h, data=data,
                       files={"file": (name, io.BytesIO(pdf or make_pdf()), "application/pdf")})


def run_worker():
    w = Worker()
    n = 0
    while w.run_once():
        n += 1
        assert n < 20
    return n


def files_on_disk():
    root = get_settings().data_dir
    return [p for k in ("uploads", "pages", "crops", "exports") if (root / k).exists() for p in (root / k).rglob("*") if p.is_file()]


def test_upload_refuse_non_pdf(client, make_user, login):
    make_user()
    h = login()
    r = client.post("/api/documents", headers=h, files={"file": ("x.pdf", io.BytesIO(b"MZ\x90 pas un pdf"), "application/pdf")})
    assert r.status_code == 415
    pdf = ("x.pdf", io.BytesIO(make_pdf()), "application/pdf")
    assert client.post("/api/documents", headers=h, data={"preprocess": "bidon"}, files={"file": pdf}).status_code == 422
    pdf = ("x.pdf", io.BytesIO(make_pdf()), "application/pdf")
    assert client.post("/api/documents", headers=h, data={"llm": "auto", "ollama_model": "qwen3.5:cloud"},
                       files={"file": pdf}).status_code == 422


def test_upload_plafonne(client, make_user, login):
    make_user("root", role="admin")
    h = login("root")
    assert client.put("/api/admin/settings", headers=h, json={"max_pages": 1}).status_code == 200
    assert upload(client, h, make_pdf(2)).status_code == 413
    assert client.put("/api/admin/settings", headers=h, json={"max_pages": 10, "max_upload_mb": 1}).status_code == 200
    assert upload(client, h, make_pdf(1)).status_code == 201


def test_nom_sur_disque_aleatoire_et_nom_assaini(client, make_user, login):
    make_user()
    h = login()
    r = upload(client, h, name="../../etc/passwd<script>.pdf")
    assert r.status_code == 201
    assert "/" not in r.json()["filename"] and "<" not in r.json()["filename"]
    names = [p.name for p in files_on_disk()]
    assert len(names) == 1 and "passwd" not in names[0] and names[0].endswith(".pdf")


def test_pipeline_complet_sans_relecture(client, make_user, login):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(2)).json()
    assert doc["status"] == "queued"
    run_worker()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "done" and d["progress"] == 1.0 and d["page_count"] == 2 and d["has_export"]
    r = client.get(f"/api/documents/{doc['id']}/export.docx", headers=h)
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "Mon%20scan.docx" in r.headers["content-disposition"]
    with session_scope() as db:  # images prétraitées temporaires effacées à la fin du job
        assert all(p.preprocessed_path is None for p in db.get(Document, doc["id"]).pages)
    page = client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()
    labels = [b["label"] for b in page["blocks"]]
    assert {"SectionHeader", "Table", "Picture"} <= set(labels)
    assert client.get(page["image_url"], headers=h).status_code == 200
    pic = next(b for b in page["blocks"] if b["label"] == "Picture")
    assert pic["has_crop"] and client.get(f"/api/blocks/{pic['id']}/crop", headers=h).status_code == 200
    ar = next(b for b in page["blocks"] if "العربية" in b["html"])
    assert ar["direction"] == "rtl" and ar["to_check"]  # confiance basse -> « à vérifier »


def test_relecture_edition_et_finalisation(client, make_user, login):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1), review=True).json()
    run_worker()
    assert client.get(f"/api/documents/{doc['id']}", headers=h).json()["status"] == "review"
    assert client.get(f"/api/documents/{doc['id']}/export.docx", headers=h).status_code == 404
    page = client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()
    txt = next(b for b in page["blocks"] if b["label"] == "Text" and "paragraphe" in b["html"])
    r = client.patch(f"/api/blocks/{txt['id']}", headers=h, json={"html": "Texte <b>corrigé</b><script>alert(1)</script>"})
    assert r.status_code == 200 and "script" not in r.json()["html"] and "<b>corrigé</b>" in r.json()["html"]
    assert client.post(f"/api/documents/{doc['id']}/finalize", headers=h).status_code == 200
    run_worker()
    assert client.get(f"/api/documents/{doc['id']}", headers=h).json()["status"] == "done"
    from docx import Document as Docx

    out = client.get(f"/api/documents/{doc['id']}/export.docx", headers=h).content
    text = "\n".join(p.text for p in Docx(io.BytesIO(out)).paragraphs)
    assert "Texte corrigé" in text and "alert" not in text
    with session_scope() as db:
        assert db.query(Correction).filter_by(source="user").count() == 1


def test_isolation_entre_utilisateurs(client, make_user, login):
    make_user("alice")
    make_user("bob", "Secret456789")
    ha, hb = login("alice"), login("bob", "Secret456789")
    doc = upload(client, ha, make_pdf(1), review=True).json()
    run_worker()
    did = doc["id"]
    bid = client.get(f"/api/documents/{did}/pages/0", headers=ha).json()["blocks"][0]["id"]
    for method, url in [("get", f"/api/documents/{did}"), ("get", f"/api/documents/{did}/pages/0"),
                        ("get", f"/api/documents/{did}/pages/0/image"), ("get", f"/api/documents/{did}/export.docx"),
                        ("delete", f"/api/documents/{did}"), ("post", f"/api/documents/{did}/finalize"),
                        ("get", f"/api/blocks/{bid}/crop"), ("post", f"/api/documents/{did}/retry")]:
        assert getattr(client, method)(url, headers=hb).status_code == 404, url
    assert client.patch(f"/api/blocks/{bid}", headers=hb, json={"html": "pirate"}).status_code == 404
    assert client.get("/api/documents", headers=hb).json()["total"] == 0
    assert client.get("/api/documents", headers=ha).json()["total"] == 1


def test_suppression_reelle(client, make_user, login):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(2)).json()
    run_worker()
    assert files_on_disk()
    assert client.delete(f"/api/documents/{doc['id']}", headers=h).status_code == 204
    assert not files_on_disk()
    with session_scope() as db:
        assert db.query(Document).count() == 0 and db.query(Block).count() == 0 and db.query(Job).count() == 0
        assert db.query(AuditLog).filter_by(action="delete").count() == 1  # l'audit survit à la suppression


def test_worker_tue_reprise(client, make_user, login):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1)).json()
    with session_scope() as db:  # job « running » abandonné par un worker tué il y a longtemps
        j = db.query(Job).first()
        j.status, j.locked_at = JobStatus.running.value, utcnow() - timedelta(hours=2)
    run_worker()
    assert client.get(f"/api/documents/{doc['id']}", headers=h).json()["status"] == "done"


def test_page_en_echec_ne_fait_pas_echouer_le_document(client, make_user, login, monkeypatch):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(2)).json()
    from app.services.ocr.dummy import DummyEngine

    calls = {"n": 0}
    real = DummyEngine.recognize

    def flaky(self, image):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("contenu confidentiel qui ne doit pas fuiter")
        return real(self, image)

    monkeypatch.setattr(DummyEngine, "recognize", flaky)
    run_worker()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "done"
    assert [p["status"] for p in d["pages"]] == ["error", "ocr_done"]
    assert "confidentiel" not in str(d)


def test_echec_total_et_retry(client, make_user, login, monkeypatch):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1)).json()
    from app.services.ocr.dummy import DummyEngine

    def boom(self, img):
        raise RuntimeError("secret")

    monkeypatch.setattr(DummyEngine, "recognize", boom)
    run_worker()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "failed" and "secret" not in str(d)
    monkeypatch.undo()
    assert client.post(f"/api/documents/{doc['id']}/retry", headers=h).status_code == 200
    run_worker()
    assert client.get(f"/api/documents/{doc['id']}", headers=h).json()["status"] == "done"


def test_attente_gpu(client, make_user, login, monkeypatch):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1)).json()
    from app.services.ocr import OcrUnavailable
    from app.worker import runner

    def no_gpu(self, db):
        raise OcrUnavailable("vram")

    monkeypatch.setattr(runner.Worker, "acquire_engine", no_gpu)
    assert Worker().run_once()
    assert client.get(f"/api/documents/{doc['id']}", headers=h).json()["status"] == "waiting_gpu"
    assert not Worker().run_once()  # pas de réessai immédiat : le job attend quelques secondes
    with session_scope() as db:
        j = db.query(Job).first()
        assert j.status == "queued" and j.attempts == 0


def test_suppression_refusee_pendant_le_traitement(client, make_user, login):
    from app.models import DocStatus

    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1)).json()
    with session_scope() as db:
        db.get(Document, doc["id"]).status = DocStatus.ocr.value
    assert client.delete(f"/api/documents/{doc['id']}", headers=h).status_code == 409
    with session_scope() as db:
        db.get(Document, doc["id"]).status = DocStatus.queued.value
    assert client.delete(f"/api/documents/{doc['id']}", headers=h).status_code == 204  # en file : suppression possible
    run_worker()  # le job a disparu avec le document : rien à traiter, aucun plantage


def test_document_supprime_pendant_le_traitement(client, make_user, login, monkeypatch):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1)).json()
    from app.services import pipeline

    def vanish(db, d):
        db.delete(d)
        db.commit()
        raise RuntimeError("document disparu")

    monkeypatch.setattr(pipeline, "render_and_preprocess", vanish)
    assert Worker().run_once()  # ne plante pas
    with session_scope() as db:
        assert db.query(Document).count() == 0


def test_relecture_change_le_type_et_supprime_un_bloc(client, make_user, login):
    """Correction d'une détection fausse : un texte devient une image (avec sa découpe), un bloc parasite est supprimé."""
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1), review=True).json()
    run_worker()
    page = client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()
    text = next(b for b in page["blocks"] if b["label"] == "Text")
    r = client.patch(f"/api/blocks/{text['id']}", headers=h, json={"label": "Picture"})
    assert r.status_code == 200 and r.json()["label"] == "Picture" and r.json()["has_crop"]
    assert client.get(f"/api/blocks/{text['id']}/crop", headers=h).status_code == 200
    assert client.patch(f"/api/blocks/{text['id']}", headers=h, json={"label": "Script"}).status_code == 422
    other = next(b for b in page["blocks"] if b["id"] != text["id"])
    assert client.delete(f"/api/blocks/{other['id']}", headers=h).status_code == 204
    ids = [b["id"] for b in client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()["blocks"]]
    assert other["id"] not in ids and text["id"] in ids
    # un autre utilisateur ne peut rien supprimer
    make_user("bob")
    hb = login("bob")
    assert client.delete(f"/api/blocks/{text['id']}", headers=hb).status_code == 404
