from app.services.ocr.layout_refine import refine


def test_bloc_agrandi_jusqu_aux_lignes_rognees():
    # bloc décalé vers la gauche : le début des lignes arabes (à droite) est hors de la boîte
    blocks = [("Text", (100, 100, 480, 200))]
    lines = [(110, 105, 520, 140), (110, 150, 520, 185)]
    out = refine(blocks, lines, 1000, 1000)
    assert len(out) == 1
    x0, y0, x1, y1 = out[0].bbox
    assert x1 >= 520 and y0 <= 105 and y1 >= 185


def test_lignes_orphelines_deviennent_un_bloc_dans_l_ordre():
    blocks = [("Text", (100, 100, 900, 200)), ("Text", (100, 600, 900, 700))]
    lines = [(105, 110, 890, 140), (110, 300, 880, 335), (110, 345, 700, 380), (105, 610, 890, 640)]
    out = refine(blocks, lines, 1000, 1000)
    assert [b.source for b in out] == [0, None, 1]
    assert out[1].label == "Text" and out[1].bbox[1] <= 300 and out[1].bbox[3] >= 380


def test_pas_d_empietement_sur_la_colonne_voisine():
    # une ligne détectée à cheval sur deux colonnes ne doit pas fusionner les colonnes
    blocks = [("Text", (100, 100, 480, 300)), ("Text", (520, 100, 900, 300))]
    lines = [(100, 110, 900, 140)]
    out = refine(blocks, lines, 1000, 1000)
    assert out[0].bbox[2] < 520 and out[1].bbox[0] > 480


def test_lignes_des_tableaux_et_images_ignorees():
    blocks = [("Table", (100, 100, 900, 500)), ("Picture", (100, 600, 400, 800))]
    lines = [(120, 120, 880, 150), (150, 650, 350, 680)]
    out = refine(blocks, lines, 1000, 1000)
    assert len(out) == 2 and out[0].bbox == (100, 100, 900, 500)


def test_texte_sous_un_tampon_lu_quand_l_image_est_transparente():
    blocks = [("Picture", (100, 100, 400, 400)), ("Text", (500, 100, 900, 400))]
    lines = [(150, 230, 380, 270)]  # texte noir sous le tampon
    assert len(refine(blocks, lines, 1000, 1000)) == 2  # image opaque : la ligne est ignorée
    out = refine(blocks, lines, 1000, 1000, see_through={0})
    assert [b.source for b in out] == [0, None, 1] and out[1].label == "Text"  # lu juste après le tampon


def test_tampon_efface_pour_la_lecture_seulement():
    import cv2
    import numpy as np
    from app.services.ocr.layout_refine import has_printed_text
    from app.services.stamps import erase_stamps

    page = np.full((800, 600, 3), 235, np.uint8)
    cv2.putText(page, "ABONNEMENT", (60, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (25, 25, 25), 3)
    cv2.circle(page, (200, 290), 110, (200, 90, 60), 8)   # tampon bleu (BGR) posé sur le texte
    cv2.putText(page, "TAMPON", (120, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 90, 60), 3)
    clean, stamps = erase_stamps(page)
    assert len(stamps) == 1
    hsv = cv2.cvtColor(clean, cv2.COLOR_BGR2HSV)
    assert int(((hsv[..., 1] > 70) & (hsv[..., 2] > 40)).sum()) < 50  # plus d'encre bleue
    gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
    assert has_printed_text(gray, stamps[0].bbox)  # le texte noir est resté
    assert (page != clean).any() and int(((cv2.cvtColor(page, cv2.COLOR_BGR2HSV)[..., 1]) > 70).sum()) > 1000  # original intact
    blank = np.full((200, 200), 235, np.uint8)
    assert not has_printed_text(blank, (0, 0, 200, 200))


def test_ligne_a_morceaux_separes_coupee_en_blocs():
    # « N° : »  « /21/MDN… »  « رقم : » sur une même ligne, séparés par de grands blancs
    blocks = [("Text", (420, 120, 1460, 165)), ("Text", (420, 600, 1460, 900))]
    lines = [(434, 127, 493, 156), (561, 126, 1342, 159), (1381, 130, 1444, 158),
             (430, 610, 1450, 640), (430, 650, 1450, 680)]
    out = refine(blocks, lines, 2339, 1654)
    first = [b for b in out if b.source == 0]
    assert len(first) == 3 and first[0].bbox[2] < 520 and first[2].bbox[0] > 1360
    assert len([b for b in out if b.source == 1]) == 1  # un paragraphe de plusieurs lignes n'est pas coupé
