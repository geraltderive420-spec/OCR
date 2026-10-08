"""Confrontation de deux lectures d'un même bloc (Surya, puis PaddleOCR-VL pour les blocs douteux).

Deux modèles différents se trompent rarement de la même façon : s'ils concordent, le bloc est sûr ; sinon on garde
la lecture la plus plausible et le bloc est signalé « à vérifier ». Comparaison sur une forme canonique (lettres et
chiffres seulement, variantes de l'arabe unifiées) : la ponctuation ou les espaces ne comptent pas comme désaccord.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_VARIANTS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
AGREE = 0.92  # similarité au-delà de laquelle les deux lectures concordent


def canonical(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    s = "".join(c for c in s if not (0x064B <= ord(c) <= 0x065F or ord(c) in (0x0670, 0x0640)))
    s = s.translate(_DIGITS).translate(_VARIANTS).lower()
    return "".join(c for c in s if c.isalnum())


@dataclass
class Verdict:
    use_second: bool
    agree: bool
    similarity: float


def looping(text: str, min_repeats: int = 6) -> bool:
    """Lecture qui boucle (« و.و.و.و.و… ») : un motif court répété à la suite, même annoncé avec assurance."""
    c = canonical(text)
    for n in range(1, 6):
        for i in range(0, len(c)):
            unit = c[i:i + n]
            # Un seul caractère : 8 fois pour une lettre, 12 pour un chiffre (« 1 000 000 » n'est pas une boucle).
            times = min_repeats if n > 1 else (12 if unit.isdigit() else 8)
            if len(unit) == n and c[i:i + n * times] == unit * times:
                return True
    return False


def decide(first: str, first_conf: float | None, second: str, second_conf: float | None) -> Verdict:
    if looping(second):
        return Verdict(False, False, 0.0)  # seconde lecture inutilisable : on garde la première, à vérifier
    a, b = canonical(first), canonical(second)
    if not b:
        return Verdict(False, not a, 1.0 if not a else 0.0)
    if not a:
        return Verdict(True, False, 0.0)
    sim = fuzz.ratio(a, b) / 100
    if sim >= AGREE:
        return Verdict(False, True, sim)
    c1, c2 = first_conf if first_conf is not None else 0.5, second_conf if second_conf is not None else 0.5
    # Une lecture nettement plus longue et sûre d'elle : l'autre a probablement perdu une partie du bloc.
    if len(b) > 1.3 * len(a) and c2 >= 0.8:
        return Verdict(True, False, sim)
    return Verdict(c2 > c1 + 0.1, False, sim)
