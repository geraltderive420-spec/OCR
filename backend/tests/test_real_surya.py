"""Intégration RÉELLE : application complète + llama-server CUDA + Surya 2 sur des échantillons publics dégradés.

Désactivé par défaut (GPU requis, plusieurs minutes). Pour le lancer :

    set OCR_GPU_TESTS=1
    set OCR_LLAMA_CPP_DIR=C:\\LLAMA.CPP.CUDA\\llama.cpp\\build\\bin\\Release
    python -m pytest tests/test_real_surya.py -s

Facultatif : OCR_TEST_PAGES (pages de samples/degrades/delave_ref, séparées par des virgules) et
OCR_TEST_DOCX_OUT (chemin où enregistrer le Word produit, pour le contrôler à l'œil).

N'utilise que des documents non confidentiels (samples/degrades). Aucun texte reconnu n'est affiché : seulement des comptes.
"""
import io
import os
import time
from pathlib import Path

import pytest

from tests.test_documents import run_worker, upload

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "degrades" / "delave_ref"

pytestmark = pytest.mark.skipif(os.environ.get("OCR_GPU_TESTS") != "1", reason="test GPU : définir OCR_GPU_TESTS=1")


def images_to_pdf(names):
    from PIL import Image

    imgs = [Image.open(SAMPLES / n).convert("RGB") for n in names]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:], resolution=200)
    return buf.getvalue()


def test_pipeline_reel_surya2(client, make_user, login, monkeypatch):
    from app.core import config
    from app.core.db import session_scope
    from app.models import Block, Document, Page

    if not SAMPLES.exists():
        pytest.skip("échantillons absents")
    llama = os.environ.get("OCR_LLAMA_CPP_DIR")
    assert llama, "OCR_LLAMA_CPP_DIR requis"
    monkeypatch.setenv("OCR_OCR_BACKEND", "surya2")
    monkeypatch.setenv("OCR_LLAMA_PORT", "8091")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    config.reset_settings_cache()

    make_user()
    h = login()
    # OCR_TEST_PAGES="A2010001_p019.png,F2020001_p015.png" pour choisir les pages (défaut : les deux premières).
    chosen = os.environ.get("OCR_TEST_PAGES")
    names = chosen.split(",") if chosen else sorted(p.name for p in SAMPLES.glob("*.png"))[:2]
    assert names
    doc = upload(client, h, images_to_pdf(names), review=False).json()

    from app.worker.runner import Worker

    w = Worker()
    t0 = time.time()
    try:
        while w.run_once():
            pass
    finally:
        w.release_engine(force=True)  # libère la VRAM et arrête llama-server quoi qu'il arrive
    seconds = time.time() - t0

    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "done", d
    with session_scope() as db:
        pages = db.query(Page).filter_by(document_id=doc["id"]).all()
        blocks = db.query(Block).join(Page).filter(Page.document_id == doc["id"]).all()
        labels = {}
        for b in blocks:
            labels[b.label] = labels.get(b.label, 0) + 1
    print(f"\n  pages={len(pages)} blocs={len(blocks)} étiquettes={labels} durée={seconds:.0f}s")
    assert all(p.status == "ocr_done" for p in pages)
    assert len(blocks) >= 2
    out = client.get(f"/api/documents/{doc['id']}/export.docx", headers=h)
    assert out.status_code == 200 and out.content[:2] == b"PK"
    if os.environ.get("OCR_TEST_DOCX_OUT"):  # contrôle visuel du Word produit (échantillons publics seulement)
        Path(os.environ["OCR_TEST_DOCX_OUT"]).write_bytes(out.content)
    # llama-server est bien arrêté : le port est libre
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        assert s.connect_ex(("127.0.0.1", 8091)) != 0
