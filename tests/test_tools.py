"""Tests des outils de phase 0 : dégradation reproductible, mesures, et surtout confidentialité du rapport."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import degrade  # noqa: E402
import evaluate  # noqa: E402
import ocr_backends  # noqa: E402

SECRET_NAME = "dossier_secret_Dupont"
SECRET_TEXT = "Contenu confidentiel très long qui ne doit jamais apparaître dans un rapport"


def _page(path: Path) -> None:
    import cv2

    img = np.full((600, 400, 3), 255, np.uint8)
    cv2.putText(img, "Hello", (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def test_degrade_reproductible_et_efface_le_contraste(tmp_path):
    src, o1, o2 = tmp_path / "src", tmp_path / "o1", tmp_path / "o2"
    _page(src / "page.png")
    assert degrade.main([str(src), str(o1), "--profil", "tres_delave", "--seed", "3"]) == 0
    assert degrade.main([str(src), str(o2), "--profil", "tres_delave", "--seed", "3"]) == 0
    assert (o1 / "page_p001.png").read_bytes() == (o2 / "page_p001.png").read_bytes()

    import cv2

    orig = cv2.imread(str(src / "page.png"), cv2.IMREAD_GRAYSCALE)
    out = cv2.imread(str(o1 / "page_p001.png"), cv2.IMREAD_GRAYSCALE)
    assert out.shape == orig.shape
    # l'encre noire (~0,8 % des pixels) est bien délavée ; p0,5 ignore les rares taches ponctuelles
    assert np.percentile(orig, 0.5) == 0 and np.percentile(out, 0.5) > 60


def test_degrade_seed_differente_donne_resultat_different(tmp_path):
    src = tmp_path / "src"
    _page(src / "p.png")
    degrade.main([str(src), str(tmp_path / "a"), "--seed", "1"])
    degrade.main([str(src), str(tmp_path / "b"), "--seed", "2"])
    assert (tmp_path / "a" / "p_p001.png").read_bytes() != (tmp_path / "b" / "p_p001.png").read_bytes()


def test_cer_par_ecriture():
    ref = "Bonjour le monde مرحبا بالعالم"
    assert evaluate.cer_by_script(ref, ref) == {"global": 0.0, "arabe": 0.0, "latin": 0.0}
    c = evaluate.cer_by_script(ref, "Bonjour le monde مرحبا")  # arabe tronqué, latin intact
    assert c["latin"] == 0.0 and c["arabe"] > 0.3


def test_normalisation_ignore_les_diacritiques_arabes():
    assert evaluate.normalize("مَرْحَبًا") == evaluate.normalize("مرحبا")


class _LeakyBackend:
    """Backend qui renvoie du texte secret et lève une exception contenant du texte secret."""

    name = "leaky"

    def __init__(self):
        self.calls = 0

    def load(self):
        pass

    def run(self, image):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError(SECRET_TEXT)
        return ocr_backends.PageResult([ocr_backends.Block("Text", SECRET_TEXT, 0.9, (0, 0, 10, 10))])

    def close(self):
        pass


def test_rapport_ne_contient_ni_texte_ni_nom_de_fichier(tmp_path):
    for i in range(3):
        _page(tmp_path / "in" / f"{SECRET_NAME}_{i}.png")
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / f"{SECRET_NAME}_0_p001.txt").write_text(SECRET_TEXT, encoding="utf-8")

    pages = evaluate.load_pages(tmp_path / "in", 200)
    report = evaluate.evaluate(_LeakyBackend(), pages, ref, warmup=False)
    dump = json.dumps(report, ensure_ascii=False)

    assert SECRET_NAME not in dump
    assert "confidentiel" not in dump and "Dupont" not in dump
    assert report["pages"] == {"total": 3, "reussies": 2, "en_erreur": 1}
    assert report["erreurs_par_type"] == {"RuntimeError": 1}  # le type, jamais le message
    assert [p["page"] for p in report["detail_pages"]] == [1, 2, 3]


def test_cli_dummy_ecrit_un_rapport(tmp_path):
    _page(tmp_path / "in" / "a.png")
    out = tmp_path / "r" / "rapport.json"
    assert evaluate.main([str(tmp_path / "in"), "--backend", "dummy", "--sortie", str(out), "--sans-echauffement"]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["blocs_par_etiquette"] == {"SectionHeader": 1, "Table": 1, "Text": 1}
    assert rep["detail_pages"][0]["tableaux"] == 1


def test_mesure_detection_contre_reference(tmp_path):
    _page(tmp_path / "in" / "x.png")
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "x.json").write_text(json.dumps({"tables": 3, "images": 0}), encoding="utf-8")  # le dummy trouve 1 tableau
    pages = evaluate.load_pages(tmp_path / "in", 200)
    rep = evaluate.evaluate(ocr_backends.DummyBackend(), pages, ref, warmup=False)
    assert rep["detection_ecart_absolu_moyen"] == {"tables": 2.0, "images": 0.0}


def test_html_to_text():
    assert ocr_backends.html_to_text("<table><tr><td>A</td><td>B&amp;C</td></tr></table>") == "A B&C"


def test_reference_appariee_avec_image_deja_nommee_page(tmp_path):
    """Une image 'X_p001.png' doit retrouver 'X_p001.txt' (régression : le nom recevait un second _p001)."""
    _page(tmp_path / "in" / "DOC_p001.png")
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "DOC_p001.txt").write_text("Titre de test", encoding="utf-8")
    pages = evaluate.load_pages(tmp_path / "in", 200)
    rep = evaluate.evaluate(ocr_backends.DummyBackend(), pages, ref, warmup=False)
    assert rep["references_appariees"] == 1
    assert rep["cer"]["global"]["n"] == 1


def test_mots_insensibles_a_lordre():
    wo = evaluate.word_overlap("un deux trois quatre", "quatre trois deux un")
    assert wo == {"rappel_mots": 1.0, "precision_mots": 1.0}
    assert evaluate.cer("un deux trois quatre", "quatre trois deux un") > 0.5  # le CER, lui, s'effondre


def test_mesure_par_caracteres_insensible_aux_artefacts_de_reference():
    # la couche texte d'un PDF coupe les mots arabes et utilise les chiffres arabes-indiens
    ref = "رشي د ب ن ناص ر، ممث ل الوزارة – ٩ جمادى ١٤٤١"
    hyp = "رشيد بن ناصر، ممثل الوزارة — 9 جمادى 1441"
    assert evaluate.word_overlap(ref, hyp)["rappel_mots"] < 0.5
    m = evaluate.ngram_overlap(ref, hyp)
    assert m["rappel_car"] == 1.0 and m["precision_car"] == 1.0
