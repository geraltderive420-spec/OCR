"""Utilitaires HTML/texte partagés par le pipeline, la correction LLM et le générateur DOCX."""
from __future__ import annotations

import html as _html
import re

_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(td|th|tr|p|div|li|h\d|caption)>|<br\s*/?>", re.IGNORECASE)
_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def has_table_markup(s: str) -> bool:
    return "<table" in s.lower()


def to_text(s: str) -> str:
    """Texte brut d'un fragment HTML (une espace entre cellules et paragraphes)."""
    s = _BLOCK_END.sub(" ", s or "")
    return " ".join(_html.unescape(_TAG.sub("", s)).split())


def arabic_ratio(text: str) -> float:
    letters = _LETTER.findall(text or "")
    if not letters:
        return 0.0
    return len(_ARABIC.findall(text)) / len(letters)


def direction_of(text: str, threshold: float = 0.4) -> str:
    """'rtl' si la proportion de lettres arabes dépasse le seuil, sinon 'ltr'."""
    return "rtl" if arabic_ratio(text) >= threshold else "ltr"


def escape(text: str) -> str:
    return _html.escape(text, quote=False)


ALLOWED_TAGS = {"p", "br", "b", "strong", "i", "em", "u", "sup", "sub", "ul", "ol", "li", "table", "thead", "tbody",
                "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "caption", "div", "span"}
ALLOWED_ATTRS = {"rowspan", "colspan"}


def sanitize_html(fragment: str) -> str:
    """Liste blanche de balises, sans script ni attribut hors rowspan / colspan (le HTML des blocs vient du navigateur)."""
    from lxml import html as lxml_html

    if not fragment or not fragment.strip():
        return ""
    try:
        root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    except Exception:  # noqa: BLE001
        return escape(to_text(fragment))
    for el in list(root.iter()):
        if not isinstance(el.tag, str):  # commentaires, instructions
            el.drop_tree()
            continue
        if el is root:
            continue
        if el.tag in ("script", "style", "iframe", "object", "embed", "link", "meta", "form", "input"):
            el.drop_tree()
        elif el.tag not in ALLOWED_TAGS:
            el.drop_tag()
    for el in root.iter():
        if isinstance(el.tag, str):
            for a in list(el.attrib):
                if a not in ALLOWED_ATTRS or not str(el.attrib[a]).isdigit():
                    del el.attrib[a]
    inner = (escape(root.text) if root.text else "") + "".join(lxml_html.tostring(c, encoding="unicode") for c in root)
    return inner
