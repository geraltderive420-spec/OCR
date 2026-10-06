from docx import Document

from app.services.docx_builder import BlockModel, DocxBuilder, PageModel, parse_inline, _split_by_script


def make_pages():
    w, h = 1654, 2339
    return [
        PageModel(w, h, [
            BlockModel("PageHeader", "Ministère de test", (200, 40, 1400, 110)),
            BlockModel("SectionHeader", "<h1>Titre principal</h1>", (300, 200, 1350, 300)),
            BlockModel("Text", "Un paragraphe en <b>gras</b> et normal, référence 2024/118.", (200, 330, 1450, 520)),
            BlockModel("Text", "هذا نص عربي تجريبي رقم 45 مع كلمات", (200, 540, 1450, 640), direction="rtl"),
            BlockModel("Text", "Mixte : voir المادة 12 du décret", (200, 660, 1450, 740)),
            BlockModel("Table", "<table><tr><th>Nom</th><th>Qté</th><th>Prix</th></tr>"
                                "<tr><td>Alpha</td><td>3</td><td>10</td></tr>"
                                "<tr><td colspan='2'>Total</td><td>30</td></tr>"
                                "<tr><td rowspan='2'>Beta</td><td>1</td><td>5</td></tr>"
                                "<tr><td>2</td><td>6</td></tr></table>", (200, 800, 1450, 1200)),
            BlockModel("ListGroup", "<ul><li>Premier</li><li>Second</li></ul>", (200, 1250, 1000, 1400)),
        ]),
        PageModel(w, h, [
            BlockModel("PageHeader", "Ministère de test", (200, 40, 1400, 110)),
            BlockModel("Text", "Deuxième page.", (200, 200, 1450, 260)),
            BlockModel("PageFooter", "Page 2", (700, 2250, 950, 2300)),
        ]),
    ]


def build(tmp_path, **kw):
    out = tmp_path / "out.docx"
    DocxBuilder(make_pages(), repeated={"PageHeader": "Ministère de test"}, **kw).save(out)
    return Document(str(out))


def test_structure_de_base(tmp_path):
    d = build(tmp_path)
    styles = [p.style.name for p in d.paragraphs if p.text.strip()]
    assert "Heading 1" in styles
    assert "List Bullet" in styles
    assert len(d.tables) == 1
    # l'en-tête répété est un vrai en-tête Word, pas un paragraphe du corps
    assert "Ministère de test" in d.sections[0].header.paragraphs[0].text
    assert not any("Ministère de test" in p.text for p in d.paragraphs)
    # saut de page entre les pages du scan
    assert any('w:br w:type="page"' in p._p.xml or "w:type=\"page\"" in p._p.xml for p in d.paragraphs)


def test_tableau_fusions(tmp_path):
    t = build(tmp_path).tables[0]
    assert len(t.rows) == 5 and len(t.columns) == 3
    xml = t._tbl.xml
    assert xml.count("w:gridSpan") == 1  # « Total » sur 2 colonnes
    assert "w:vMerge" in xml  # « Beta » sur 2 lignes
    assert t.cell(0, 0).paragraphs[0].runs[0].bold


def test_arabe_bidi_et_segments(tmp_path):
    d = build(tmp_path)
    ar = next(p for p in d.paragraphs if "عربي" in p.text)
    assert "<w:bidi" in ar._p.xml
    assert any("<w:rtl" in r._r.xml for r in ar.runs)
    mixed = next(p for p in d.paragraphs if p.text.startswith("Mixte"))
    assert "<w:bidi" not in mixed._p.xml  # paragraphe de base LTR
    kinds = [("<w:rtl" in r._r.xml) for r in mixed.runs]
    assert any(kinds) and not all(kinds)  # segments arabes et latins séparés


def test_decoupage_par_ecriture():
    segs = _split_by_script("Voir المادة 12 du décret")
    assert [k for _, k in segs] == ["la", "ar", "la"]
    assert "".join(s for s, _ in segs) == "Voir المادة 12 du décret"


def test_gras_inline():
    paras = parse_inline("Un <b>mot</b> clé<br>ligne 2")
    flat = [s for p in paras for s in p]
    assert any(s.bold and s.text == "mot" for s in flat)
    assert any(s.br for s in flat)


def test_page_vide_et_orientation_paysage(tmp_path):
    out = tmp_path / "l.docx"
    DocxBuilder([PageModel(2339, 1654, [BlockModel("Text", "Paysage", (100, 100, 2000, 200))])]).save(out)
    sec = Document(str(out)).sections[0]
    assert sec.page_width > sec.page_height
    DocxBuilder([]).save(tmp_path / "v.docx")  # aucun plantage sans page
