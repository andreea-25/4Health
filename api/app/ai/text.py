"""Utilitare de text pentru română (normalizare diacritice etc.)."""
import re
import unicodedata

_CEDILLA = str.maketrans({"ş": "s", "ţ": "t", "Ş": "s", "Ţ": "t"})


def normalize(text: str) -> str:
    """minuscule, fără diacritice, spații compactate: 'Dimineața' -> 'dimineata'."""
    text = text.translate(_CEDILLA).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))
