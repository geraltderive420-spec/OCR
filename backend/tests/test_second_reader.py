from app.services.ocr.consensus import canonical, decide


def test_lectures_concordantes_malgre_ponctuation_et_chiffres():
    v = decide("رشيد بن ناصر، ممثل – ٩ جمادى", 0.8, "رشيد بن ناصر ممثل - 9 جمادى", 0.9)
    assert v.agree and not v.use_second


def test_premiere_lecture_tronquee_remplacee():
    v = decide("ETABLISSEMENT", 0.7, "ETABLISSEMENT DE LA PLATEFORME DE SYSTEMES ELECTRONIQUE", 0.95)
    assert v.use_second and not v.agree


def test_desaccord_garde_la_plus_sure_et_signale():
    v = decide("Panne des groupes", 0.95, "Pomme des grappes", 0.6)
    assert not v.use_second and not v.agree
    assert decide("", None, "texte", 0.9).use_second
    assert canonical("Électrogènes, 4°RM") == "électrogènes4rm"


def test_seconde_lecture_dans_le_pipeline(client, make_user, login, monkeypatch):
    """Faux second lecteur : un bloc douteux relu, remplacé (correction « ocr2 » annulable) et signalé."""
    from app.core import config
    from app.core.db import session_scope
    from app.models import Block, Correction
    from app.services.ocr import second_reader
    from tests.conftest import make_pdf
    from tests.test_documents import run_worker, upload

    class FakeReader:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def stop(self): pass
        def read(self, crop, max_tokens=1024): return "Texte relu entièrement par le second lecteur, bien plus long", 0.97

    monkeypatch.setenv("OCR_SECOND_READER", "true")
    monkeypatch.setenv("OCR_SECOND_READER_THRESHOLD", "1.01")  # tous les blocs sont « douteux »
    config.reset_settings_cache()
    monkeypatch.setattr(second_reader, "SecondReader", FakeReader)
    monkeypatch.setattr(second_reader, "available", lambda: True)
    make_user()
    h = login()
    doc = upload(client, h, make_pdf(1), review=True).json()
    run_worker()
    d = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert d["status"] == "review" and d["options"]["second_stats"]["remplaces"] >= 1
    with session_scope() as db:
        c = db.query(Correction).filter_by(source="ocr2").first()
        assert c is not None and db.get(Block, c.block_id).to_check
    page = client.get(f"/api/documents/{doc['id']}/pages/0", headers=h).json()
    replaced = [b for b in page["blocks"] if any(x["source"] == "ocr2" for x in b["corrections"])]
    assert replaced and "second lecteur" in replaced[0]["html"]


def test_lecture_qui_boucle_refusee():
    from app.services.ocr.consensus import looping
    loop = "/21/MDN/DFM/PSE/2°RM/Q/2.ع.ق.م.ان.و.و.م.ص.ع.ق.م.ان.و.و.و.و.و.و.و.و.و.و"
    assert looping(loop) and not looping("Suite au disfonctionnement des groupes électrogènes 2020")
    assert not looping("Montant : 1 000 000 DA, soit 10 000 000 centimes")
    v = decide("21/و.د.وام.ص.ع/ق.م./ان.ع.2", 0.6, loop, 0.98)
    assert not v.use_second and not v.agree
