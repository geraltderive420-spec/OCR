"""Correction par Ollama (PLAN.md §9) : bloc par bloc, texte seul, avec garde-fous appliqués par le CODE.

- Seuls les modèles LOCAUX sont acceptés (les modèles « cloud » envoient les documents sur Internet).
- Les balises HTML sont remplacées par des jetons puis restaurées : le modèle ne voit que du texte.
- Rejet si les chiffres / dates changent, si le taux de modification dépasse le seuil, si la longueur ou la langue
  change nettement. Aucun texte de document dans les journaux.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx
from rapidfuzz.distance import Levenshtein

from app.services.app_settings import is_cloud_model
from app.services.ocr.html_utils import arabic_ratio

log = logging.getLogger("ocr.llm")

SYSTEM_PROMPT = (
    "Tu corriges uniquement les erreurs de reconnaissance optique (OCR) dans un texte en français et/ou en arabe. "
    "Règles absolues : ne change AUCUN chiffre, date, numéro, référence, nom propre ni montant ; ne reformule pas ; "
    "ne traduis pas ; n'ajoute ni ne supprime de contenu ; conserve les jetons de la forme ⟦n⟧ tels quels. "
    "Corrige seulement les caractères manifestement mal reconnus. Si tu as un doute, renvoie le texte inchangé. "
    'Réponds uniquement par un objet JSON {"texte": "..."}.'
)
SCHEMA = {"type": "object", "properties": {"texte": {"type": "string"}}, "required": ["texte"]}

_TAG = re.compile(r"(<[^>]+>)")
_NUM = re.compile(r"\d+(?:[.,/\-:]\d+)*")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LlmError(RuntimeError):
    pass


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


def tokenize(html: str) -> tuple[str, list[str]]:
    """Remplace chaque balise par ⟦n⟧ ; retourne (texte avec jetons, balises dans l'ordre)."""
    tags: list[str] = []

    def sub(m: re.Match) -> str:
        tags.append(m.group(1))
        return f"⟦{len(tags) - 1}⟧"

    return _TAG.sub(sub, html), tags


def detokenize(text: str, tags: list[str]) -> str | None:
    """Restaure les balises ; None si un jeton a disparu, été dupliqué ou inventé."""
    found = re.findall(r"⟦(\d+)⟧", text)
    if sorted(int(i) for i in found) != list(range(len(tags))):
        return None
    return re.sub(r"⟦(\d+)⟧", lambda m: tags[int(m.group(1))], text)


def numbers(text: str) -> list[str]:
    return _NUM.findall(re.sub(r"⟦\d+⟧", " ", text).translate(_ARABIC_DIGITS))


def check(before: str, after: str, max_change: float) -> Verdict:
    """Garde-fous sur le texte (jetons inclus) avant / après correction."""
    if after.strip() == before.strip():
        return Verdict(False, "inchangé")
    if numbers(before) != numbers(after):
        return Verdict(False, "chiffres modifiés")
    if re.findall(r"⟦\d+⟧", before) != re.findall(r"⟦\d+⟧", after):
        return Verdict(False, "balises modifiées")
    if before and Levenshtein.distance(before, after) / max(1, len(before)) > max_change:
        return Verdict(False, "trop de modifications")
    if before and abs(len(after) - len(before)) / max(1, len(before)) > 0.2:
        return Verdict(False, "longueur modifiée")
    if abs(arabic_ratio(before) - arabic_ratio(after)) > 0.15:
        return Verdict(False, "langue modifiée")
    return Verdict(True)


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float = 180.0, think: bool = False, keep_alive: int = 0):
        if is_cloud_model(model):
            raise LlmError("Modèle « cloud » refusé.")
        self.base, self.model, self.timeout, self.think, self.keep_alive = base_url.rstrip("/"), model, timeout, think, keep_alive

    def available(self) -> bool:
        try:
            tags = httpx.get(f"{self.base}/api/tags", timeout=5).json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return False
        return any(m.get("name") == self.model or m.get("model") == self.model for m in tags)

    def correct(self, text: str) -> str:
        payload = {
            "model": self.model, "stream": False, "format": SCHEMA, "keep_alive": self.keep_alive,
            "think": self.think,  # champ séparé pour les modèles « thinking » ; jamais enregistré
            "options": {"temperature": 0, "top_p": 0.1, "num_predict": 2048 if not self.think else 6144},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": json.dumps({"texte": text}, ensure_ascii=False)}],
        }
        try:
            r = httpx.post(f"{self.base}/api/chat", json=payload, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise LlmError(type(e).__name__) from e
        if r.status_code == 400 and "think" in r.text.lower():  # le modèle ne gère pas « think » : on réessaie sans
            payload.pop("think")
            r = httpx.post(f"{self.base}/api/chat", json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise LlmError(f"HTTP {r.status_code}")
        content = (r.json().get("message") or {}).get("content", "")  # le raisonnement est dans message.thinking : ignoré
        content = _THINK.sub("", content).strip()
        try:
            return str(json.loads(content)["texte"])
        except (ValueError, KeyError, TypeError) as e:
            raise LlmError("réponse non conforme") from e

    def unload(self) -> None:
        try:
            httpx.post(f"{self.base}/api/generate", json={"model": self.model, "keep_alive": 0}, timeout=15)
        except httpx.HTTPError:
            pass


def correct_block(client: OllamaClient, html: str, max_change: float) -> tuple[str, Verdict]:
    """Retourne (html corrigé ou d'origine, verdict). Les tableaux sont traités cellule par cellule."""
    if "<table" in html.lower():
        return _correct_table(client, html, max_change)
    text, tags = tokenize(html)
    if not re.search(r"[^\W\d_]", text):
        return html, Verdict(False, "sans texte")
    answer = client.correct(text)
    verdict = check(text, answer, max_change)
    if not verdict.ok:
        return html, verdict
    restored = detokenize(answer, tags)
    if restored is None:
        return html, Verdict(False, "balises modifiées")
    return restored, verdict


def _correct_table(client: OllamaClient, html: str, max_change: float) -> tuple[str, Verdict]:
    parts = re.split(r"(<t[dh][^>]*>.*?</t[dh]>)", html, flags=re.DOTALL | re.IGNORECASE)
    changed, out = False, []
    for part in parts:
        m = re.match(r"(<t[dh][^>]*>)(.*?)(</t[dh]>)$", part, flags=re.DOTALL | re.IGNORECASE)
        if not m or not re.search(r"[^\W\d_]", m.group(2)):
            out.append(part)
            continue
        new, v = correct_block(client, m.group(2), max_change)
        out.append(m.group(1) + new + m.group(3))
        changed = changed or (v.ok and new != m.group(2))
    return "".join(out), Verdict(changed, "" if changed else "inchangé")
