"""Nettoyage et normalisation des chaînes (noms, e-mails, téléphones, codes postaux, statuts)."""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

_NULL_TOKENS = {"", "NULL", "N/A", "NA", "NONE", "-", "?"}
_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
_SPACES = re.compile(r"\s+")


def clean_str(value: object) -> str | None:
    """Supprime les espaces superflus ; None pour les valeurs vides ou jetons « NULL »."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = _SPACES.sub(" ", str(value)).strip()
    return None if text.upper() in _NULL_TOKENS else text


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def match_key(text: str | None) -> str | None:
    """Clé de comparaison pour le rapprochement : majuscules, sans accents ni ponctuation."""
    text = clean_str(text)
    if text is None:
        return None
    text = strip_accents(text).upper()
    text = re.sub(r"[^A-Z ]", " ", text)
    return _SPACES.sub(" ", text).strip() or None


def normalize_email(value: object) -> str | None:
    """Minuscules, sans espaces ; None si le format est invalide."""
    text = clean_str(value)
    if text is None:
        return None
    text = text.replace(" ", "").lower()
    return text if _EMAIL_RE.match(text) else None


def normalize_phone(value: object, default_country: str = "33") -> str | None:
    """Normalise un numéro au format E.164 (+33612345678). None si inexploitable."""
    text = clean_str(value)
    if text is None:
        return None
    digits = re.sub(r"[^\d+]", "", text)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+"):
        result = "+" + re.sub(r"\D", "", digits[1:])
    elif digits.startswith("0") and len(digits) == 10:
        result = f"+{default_country}{digits[1:]}"
    else:
        return None
    return result if 9 <= len(result) - 1 <= 15 else None


def normalize_postal_code(value: object) -> str | None:
    """Code postal français sur 5 chiffres (rétablit le zéro initial perdu : 6000 -> 06000)."""
    text = clean_str(value)
    if text is None:
        return None
    text = text.replace(" ", "")
    if text.isdigit() and len(text) == 4:
        text = "0" + text
    return text if re.fullmatch(r"\d{5}", text) else None


def split_full_name(full_name: object) -> tuple[str | None, str | None]:
    """Découpe AP_FULL_NAME en (nom, prénom).

    Règles (par ordre de priorité) :
    1. « NOM, Prénom » : la virgule sépare le nom du prénom ;
    2. les mots entièrement en MAJUSCULES forment le nom (« Jean-Pierre DUPONT ») ;
    3. sinon le dernier mot est le nom (« Marie Martin »).
    """
    text = clean_str(full_name)
    if text is None:
        return None, None
    if "," in text:
        nom, prenom = (part.strip() for part in text.split(",", 1))
        return (nom.upper() or None), (prenom.title() or None)
    words = text.split(" ")
    if len(words) == 1:
        return words[0].upper(), None
    upper_words = [w for w in words if w.isupper() and len(w) > 1]
    if upper_words and len(upper_words) < len(words):
        nom = " ".join(upper_words)
        prenom = " ".join(w for w in words if w not in upper_words)
        return nom, prenom.title()
    return words[-1].upper(), " ".join(words[:-1]).title()


def normalize_code(value: object) -> str | None:
    """Code de référentiel : majuscules, sans accents, espaces remplacés par « _ »."""
    text = clean_str(value)
    if text is None:
        return None
    return strip_accents(text).upper().replace(" ", "_").replace("-", "_")
