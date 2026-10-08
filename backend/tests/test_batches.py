"""Traitement par lot, annulation, équité de la file, images en entrée."""
import io
import zipfile

import numpy as np
from PIL import Image

from app.core.db import session_scope
from app.models import Document, Job
from app.worker.runner import Worker
from tests.conftest import make_pdf
from tests.test_documents import run_worker, upload


def image_bytes(fmt="PNG", frames=1):
    imgs = [Image.fromarray(np.full((800, 600, 3), 255, np.uint8)) for _ in range(frames)]
    buf = io.BytesIO()
    if frames > 1:
        imgs[0].save(buf, format=fmt, save_all=True, append_images=imgs[1:])
    else:
        imgs[0].save(buf, format=fmt)
    return buf.getvalue()


def new_batch(client, h, name="Courriers"):
    r = client.post("/api/batches", headers=h, json={"name": name, "options": {"review": False}})
    assert r.status_code == 201, r.text
    return r.json()


def test_lot_complet_avec_images_et_zip(client, make_user, login):
    make_user()
    h = login()
    b = new_batch(client, h)
    assert upload(client, h, make_pdf(1), name="a.pdf", batch_id=b["id"]).status_code == 201
    assert upload(client, h, make_pdf(1), name="a.pdf", batch_id=b["id"]).status_code == 201  # même nom
    r = client.post("/api/documents", headers=h, data={"batch_id": b["id"]},
                    files={"file": ("scan.png", io.BytesIO(image_bytes("PNG")), "image/png")})
    assert r.status_code == 201 and r.json()["page_count"] == 1
    r = client.post("/api/documents", headers=h, data={"batch_id": b["id"]},
                    files={"file": ("multi.tif", io.BytesIO(image_bytes("TIFF", 3)), "image/tiff")})
    assert r.status_code == 201 and r.json()["page_count"] == 3
    r = client.post("/api/documents", headers=h, data={"batch_id": b["id"]},
                    files={"file": ("x.txt", io.BytesIO(b"bonjour"), "text/plain")})
    assert r.status_code == 415  # un fichier refusé n'empêche pas les autres

    lot = client.get(f"/api/batches/{b['id']}", headers=h).json()
    assert lot["total"] == 4 and lot["counts"]["queued"] == 4 and lot["active"] == 4
    run_worker()
    lot = client.get(f"/api/batches/{b['id']}", headers=h).json()
    assert lot["counts"]["done"] == 4 and lot["progress"] == 1.0 and lot["active"] == 0
    assert all(d["batch_name"] == "Courriers" for d in lot["documents"])

    z = client.get(f"/api/batches/{b['id']}/export.zip", headers=h)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(z.content)).namelist())
    assert names == ["a (2).docx", "a.docx", "multi.docx", "scan.docx"]

    listed = client.get("/api/batches", headers=h).json()
    assert listed["total"] == 1 and listed["items"][0]["counts"]["done"] == 4
    filtered = client.get(f"/api/documents?batch_id={b['id']}&q=scan", headers=h).json()
    assert [d["filename"] for d in filtered["items"]] == ["scan.png"]
    assert filtered["counts"]["done"] == 4


def test_lot_isole_par_utilisateur(client, make_user, login):
    make_user()
    make_user("bob")
    h = login()
    b = new_batch(client, h)
    hb = login("bob")
    assert client.get(f"/api/batches/{b['id']}", headers=hb).status_code == 404
    assert upload(client, hb, make_pdf(1), batch_id=b["id"]).status_code == 404  # pas dans le lot d'un autre
    assert client.get(f"/api/batches/{b['id']}/export.zip", headers=hb).status_code == 404
    assert client.delete(f"/api/batches/{b['id']}", headers=hb).status_code == 404


def test_annulation_relance_et_suppression_du_lot(client, make_user, login):
    make_user()
    h = login()
    b = new_batch(client, h)
    ids = [upload(client, h, make_pdf(1), batch_id=b["id"]).json()["id"] for _ in range(3)]
    r = client.post(f"/api/batches/{b['id']}/cancel", headers=h).json()
    assert r["affected"] == 3 and r["counts"]["cancelled"] == 3
    assert run_worker() == 0  # plus rien à traiter
    r = client.post(f"/api/batches/{b['id']}/retry", headers=h).json()
    assert r["affected"] == 3 and r["counts"]["queued"] == 3
    run_worker()
    assert client.get(f"/api/batches/{b['id']}", headers=h).json()["counts"]["done"] == 3
    assert client.delete(f"/api/batches/{b['id']}", headers=h).status_code == 204
    with session_scope() as db:
        assert db.query(Document).filter(Document.id.in_(ids)).count() == 0


def test_finaliser_tout_le_lot(client, make_user, login):
    make_user()
    h = login()
    b = new_batch(client, h)
    for _ in range(2):
        upload(client, h, make_pdf(1), batch_id=b["id"], review=True)
    run_worker()
    assert client.get(f"/api/batches/{b['id']}", headers=h).json()["counts"]["review"] == 2
    assert client.post(f"/api/batches/{b['id']}/finalize", headers=h).json()["affected"] == 2
    run_worker()
    lot = client.get(f"/api/batches/{b['id']}", headers=h).json()
    assert lot["counts"]["done"] == 2 and lot["exportable"] == 2


def test_annulation_pendant_le_traitement(client, make_user, login, monkeypatch):
    """Annulation demandée pendant l'OCR : le worker s'arrête à la page suivante, le document est « annulé »."""
    from app.services.ocr.dummy import DummyEngine

    make_user()
    h = login()
    doc = upload(client, h, make_pdf(3)).json()
    real = DummyEngine.recognize
    calls = {"n": 0}

    def recognize_then_cancel(self, image):
        calls["n"] += 1
        with session_scope() as db:
            db.get(Document, doc["id"]).cancel_requested = True
        return real(self, image)

    monkeypatch.setattr(DummyEngine, "recognize", recognize_then_cancel)
    assert Worker().run_once()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "cancelled" and calls["n"] == 1 and not d["cancel_requested"]
    monkeypatch.setattr(DummyEngine, "recognize", real)
    assert client.post(f"/api/documents/{doc['id']}/retry", headers=h).status_code == 200
    run_worker()  # pages déjà rendues reprises (image de travail effacée : rendues à nouveau)
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "done" and all(p["status"] == "ocr_done" for p in d["pages"])


def test_annulation_refusee_hors_traitement(client, make_user, login):
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1)).json()
    run_worker()
    assert client.post(f"/api/documents/{doc['id']}/cancel", headers=h).status_code == 409


def test_file_equitable_entre_utilisateurs(client, make_user, login):
    """Un gros lot d'un utilisateur ne fait pas attendre le document isolé d'un autre."""
    make_user()
    make_user("bob")
    ha, hb = login(), login("bob")
    a = [upload(client, ha, make_pdf(1)).json()["id"] for _ in range(3)]
    bob = upload(client, hb, make_pdf(1)).json()["id"]
    w = Worker()
    order = []
    for _ in range(4):
        with session_scope() as db:
            job = w.claim(db)
            order.append(job.document_id)
            w.handle(db, job)
    assert order[:2] == [a[0], bob]  # bob passe en deuxième, pas en quatrième


def test_prise_de_job_atomique(client, make_user, login):
    make_user()
    h = login()
    upload(client, h, make_pdf(1))
    w1, w2 = Worker(), Worker()
    with session_scope() as db:
        j1 = w1.claim(db)
    with session_scope() as db:
        assert j1 is not None and w2.claim(db) is None  # déjà pris : jamais traité deux fois
        assert db.get(Job, j1.id).attempts == 1
