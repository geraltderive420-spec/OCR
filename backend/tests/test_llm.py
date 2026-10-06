import json

import httpx
import pytest

from app.services import llm
from app.services.llm import LlmError, OllamaClient, check, correct_block, detokenize, tokenize


# ---------------------------------------------------------------- garde-fous (code, pas prompt)

def test_changement_de_chiffre_rejete():
    assert not check("Facture n° 2024/118 du 12/03/2024", "Facture n° 2024/181 du 12/03/2024", 0.15).ok
    assert not check("Montant 1 250,00", "Montant 1 520,00", 0.15).ok
    assert not check("Date 12/03/2024", "Date 12/03/2025", 0.15).ok


def test_chiffres_arabes_orientaux_comptent():
    assert not check("المادة 12 من القانون", "المادة ١٣ من القانون", 0.15).ok
    assert check("المادة 12 من القانون", "المادة ١٢ من القانون", 0.15).reason != "chiffres modifiés"


def test_correction_valide_acceptee():
    v = check("Le mimistère de la défense", "Le ministère de la défense", 0.15)
    assert v.ok


def test_trop_de_modifications_rejete():
    assert not check("Le ministère de la défense nationale", "Le gouvernement de la république", 0.15).ok


def test_longueur_et_langue():
    assert not check("Texte court ici présent", "Texte court ici présent et une longue addition inventée par le modèle", 0.9).ok
    assert not check("Ceci est un texte français", "هذا نص عربي كامل مختلف تماما", 1.0).ok


def test_inchange_ignore():
    assert check("identique", "identique", 0.15).reason == "inchangé"


def test_jetons_balises():
    text, tags = tokenize("Un <b>mot</b> clé<br/>fin")
    assert text == "Un ⟦0⟧mot⟦1⟧ clé⟦2⟧fin" and tags == ["<b>", "</b>", "<br/>"]
    assert detokenize(text, tags) == "Un <b>mot</b> clé<br/>fin"
    assert detokenize("Un ⟦0⟧mot clé⟦2⟧fin", tags) is None  # jeton perdu
    assert detokenize("Un ⟦0⟧⟦0⟧mot⟦1⟧⟦2⟧", tags) is None  # jeton dupliqué
    assert detokenize("Un ⟦0⟧mot⟦1⟧⟦2⟧⟦3⟧", tags) is None  # jeton inventé


# ---------------------------------------------------------------- client Ollama simulé

class FakeOllama:
    """Simule /api/chat et /api/tags ; enregistre les requêtes pour vérifier ce qui est envoyé."""

    def __init__(self, answers=None, think_supported=True):
        self.answers, self.think_supported, self.requests = answers or {}, think_supported, []

    def post(self, url, json=None, timeout=None, **kw):
        self.requests.append((url, json))
        if url.endswith("/api/chat"):
            if "think" in json and not self.think_supported:
                return httpx.Response(400, text="model does not support think")
            texte = __import__("json").loads(json["messages"][1]["content"])["texte"]
            out = self.answers.get(texte, texte)
            body = {"message": {"content": __import__("json").dumps({"texte": out}), "thinking": "raisonnement interne"}}
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={})

    def get(self, url, timeout=None, **kw):
        return httpx.Response(200, json={"models": [{"name": "gemma4:e2b"}, {"name": "qwen354:latest"}]})


@pytest.fixture()
def fake(monkeypatch):
    f = FakeOllama()
    monkeypatch.setattr(llm.httpx, "post", f.post)
    monkeypatch.setattr(llm.httpx, "get", f.get)
    return f


def test_modele_cloud_refuse():
    with pytest.raises(LlmError):
        OllamaClient("http://x", "qwen3.5:cloud")
    with pytest.raises(LlmError):
        OllamaClient("http://x", "gemma4:31b-cloud")


def test_correct_block_applique_et_conserve_les_balises(fake):
    fake.answers = {"Le mimistère de ⟦0⟧défense⟦1⟧": "Le ministère de ⟦0⟧défense⟦1⟧"}
    c = OllamaClient("http://x", "gemma4:e2b")
    new, v = correct_block(c, "Le mimistère de <b>défense</b>", 0.15)
    assert v.ok and new == "Le ministère de <b>défense</b>"
    url, payload = next(r for r in fake.requests if r[0].endswith("/api/chat"))
    assert payload["format"]["required"] == ["texte"] and payload["options"]["temperature"] == 0
    assert "<b>" not in payload["messages"][1]["content"]  # le modèle ne voit que du texte


def test_chiffre_modifie_par_le_modele_rejete(fake):
    fake.answers = {"Facture 2024/118 reçue": "Facture 2024/181 reçue"}
    new, v = correct_block(OllamaClient("http://x", "gemma4:e2b"), "Facture 2024/118 reçue", 0.5)
    assert not v.ok and new == "Facture 2024/118 reçue"


def test_tableau_cellule_par_cellule(fake):
    fake.answers = {"Quantite totale": "Quantité totale"}
    html = "<table><tr><th>Quantite totale</th><td>42</td></tr></table>"
    new, v = correct_block(OllamaClient("http://x", "gemma4:e2b"), html, 0.6)
    assert v.ok and "Quantité totale" in new and "<td>42</td>" in new
    texts = [json.loads(r[1]["messages"][1]["content"])["texte"] for r in fake.requests if r[0].endswith("/api/chat")]
    assert texts == ["Quantite totale"]  # les cellules sans lettres (42) ne sont pas envoyées


def test_modele_thinking_raisonnement_jamais_enregistre(fake):
    c = OllamaClient("http://x", "gemma4:e2b", think=True)
    assert c.correct("Bonjour") == "Bonjour"
    assert fake.requests[0][1]["think"] is True  # paramètre d'API, raisonnement lu dans un champ séparé (ignoré)


def test_modele_sans_support_think_repli(monkeypatch):
    f = FakeOllama(think_supported=False)
    monkeypatch.setattr(llm.httpx, "post", f.post)
    assert OllamaClient("http://x", "m", think=True).correct("Salut") == "Salut"
    assert "think" not in f.requests[-1][1]


def test_balises_think_dans_la_reponse_retirees(monkeypatch):
    def post(url, json=None, **kw):
        return httpx.Response(200, json={"message": {"content": '<think>réfléchit</think>{"texte": "Bonjour"}'}})
    monkeypatch.setattr(llm.httpx, "post", post)
    assert OllamaClient("http://x", "m").correct("Bonjour") == "Bonjour"


def test_ollama_arrete(monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("refusé")
    monkeypatch.setattr(llm.httpx, "get", down)
    assert not OllamaClient("http://x", "gemma4:e2b").available()
