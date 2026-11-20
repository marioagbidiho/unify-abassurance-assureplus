"""Pseudonymisation (HMAC-SHA256) et chiffrement (AES-256-GCM) des données personnelles.

* ``pseudonymize`` produit un jeton stable, non réversible sans la clé : il permet les
  jointures et le rapprochement MDM sans exposer l'e-mail ou le nom (RGPD, minimisation).
* ``encrypt`` / ``decrypt`` protègent les données à restituer (n° fiscal, téléphone).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Quantificateurs possessifs (++) : pas de retour arrière -> temps linéaire (anti-ReDoS).
# Les e-mails sont repérés sans expression régulière (découpage par blancs), en O(n).
_WHITESPACE = re.compile(r"(\s+)")
_TRAILING_PUNCT = ".,;:!?)]}>\"'"
_PHONE_IN_TEXT = re.compile(r"(?:\+33|0)\s?[1-9](?:[\s.\-]?+\d{2}){4}")
_IBAN_IN_TEXT = re.compile(r"\bFR\d{2}(?:\s?\d{4}){5}\s?\d{3}\b", re.IGNORECASE)


def pseudonymize(value: str | None, key: str) -> str | None:
    """Jeton HMAC-SHA256 (hexadécimal, 64 caractères) ; None si la valeur est absente."""
    if value is None:
        return None
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def encrypt(value: str | None, key_hex: str) -> str | None:
    """Chiffre en AES-256-GCM ; retourne nonce + texte chiffré en base64."""
    if value is None:
        return None
    nonce = os.urandom(12)
    cipher = AESGCM(bytes.fromhex(key_hex)).encrypt(nonce, value.encode(), None)
    return base64.b64encode(nonce + cipher).decode()


def decrypt(token: str | None, key_hex: str) -> str | None:
    if token is None:
        return None
    raw = base64.b64decode(token)
    return AESGCM(bytes.fromhex(key_hex)).decrypt(raw[:12], raw[12:], None).decode()


def _is_email(token: str) -> bool:
    local, at, domain = token.rstrip(_TRAILING_PUNCT).partition("@")
    labels = domain.split(".")
    return bool(at and local and len(labels) >= 2 and all(labels))


def _mask_emails(text: str) -> str:
    parts = _WHITESPACE.split(text)
    return "".join("[EMAIL]" if _is_email(part) else part for part in parts)


def redact_free_text(text: str | None) -> str | None:
    """Masque les données personnelles évidentes dans le texte libre des sinistres."""
    if text is None:
        return None
    text = _mask_emails(text)
    text = _IBAN_IN_TEXT.sub("[IBAN]", text)
    return _PHONE_IN_TEXT.sub("[TEL]", text)
