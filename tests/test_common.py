"""Tests unitaires des fonctions de nettoyage (dates, textes, identifiants, sécurité)."""

from datetime import date, datetime

import pandas as pd
import pytest

from unify.common import dates, security, text
from unify.common.ids import canonical_id


# ------------------------------------------------------------------ dates
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1985-06-13", date(1985, 6, 13)),
        ("13/06/1985", date(1985, 6, 13)),  # format français rencontré côté AP
        ("1985-13-45", None),  # date impossible -> None (quarantaine)
        ("", None),
        ("NULL", None),
        (None, None),
        (float("nan"), None),
        (datetime(2020, 1, 2, 10, 0), date(2020, 1, 2)),
        (pd.Timestamp("2021-03-04"), date(2021, 3, 4)),
        (date(1999, 12, 31), date(1999, 12, 31)),
        ("2020-01-02 10:00:00", date(2020, 1, 2)),
    ],
)
def test_parse_date(raw, expected):
    assert dates.parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-03-01 14:30:00", datetime(2026, 3, 1, 14, 30)),
        ("2026-03-01T14:30:00", datetime(2026, 3, 1, 14, 30)),
        ("2026-36-01 14:30:00", None),
        ("2026-03-01", datetime(2026, 3, 1)),
        (date(2026, 3, 1), datetime(2026, 3, 1)),
        (pd.Timestamp("2026-03-01 08:00"), datetime(2026, 3, 1, 8)),
        (None, None),
    ],
)
def test_parse_datetime(raw, expected):
    assert dates.parse_datetime(raw) == expected


def test_to_utc_handles_summer_and_winter_time():
    assert dates.to_utc(datetime(2026, 1, 15, 12, 0)) == datetime(2026, 1, 15, 11, 0)  # UTC+1
    assert dates.to_utc(datetime(2026, 7, 15, 12, 0)) == datetime(2026, 7, 15, 10, 0)  # UTC+2
    assert dates.to_utc(None) is None


def test_to_utc_midnight_shifts_to_previous_day():
    """Une date AB sans heure (00:00 Paris) devient la veille à 22h/23h UTC."""
    assert dates.to_utc(datetime(2026, 6, 1)) == datetime(2026, 5, 31, 22, 0)


def test_age_at():
    assert dates.age_at(date(2000, 7, 1), date(2026, 6, 30)) == 25
    assert dates.age_at(date(2000, 6, 30), date(2026, 6, 30)) == 26
    assert dates.age_at(None, date(2026, 6, 30)) is None


# ------------------------------------------------------------------ textes
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Jean   Pierre ", "Jean Pierre"),
        ("NULL", None),
        ("n/a", None),
        ("", None),
        (None, None),
        (float("nan"), None),
        (42, "42"),
    ],
)
def test_clean_str(raw, expected):
    assert text.clean_str(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        (" Jean.Dupont@Gmail.COM ", "jean.dupont@gmail.com"),
        ("jean.dupont at gmail.com", None),
        ("pas-un-email", None),
        (None, None),
    ],
)
def test_normalize_email(raw, expected):
    assert text.normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("06 12 34 56 78", "+33612345678"),
        ("0612345678", "+33612345678"),
        ("+33 6 12 34 56 78", "+33612345678"),
        ("0033612345678", "+33612345678"),
        ("12345", None),
        ("abc", None),
        (None, None),
    ],
)
def test_normalize_phone(raw, expected):
    assert text.normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("75011", "75011"),
        ("6000", "06000"),
        (" 13 008 ", "13008"),
        ("ABCDE", None),
        (None, None),
    ],
)
def test_normalize_postal_code(raw, expected):
    assert text.normalize_postal_code(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Jean-Pierre DUPONT", ("DUPONT", "Jean-Pierre")),
        ("DUPONT, Jean-Pierre", ("DUPONT", "Jean-Pierre")),
        ("Marie Martin", ("MARTIN", "Marie")),
        ("Anne Sophie LE GALL", ("LE GALL", "Anne Sophie")),
        ("Cher", ("CHER", None)),
        (None, (None, None)),
    ],
)
def test_split_full_name(raw, expected):
    assert text.split_full_name(raw) == expected


def test_match_key_and_codes():
    assert text.match_key("  Élodie-Anne ") == "ELODIE ANNE"
    assert text.match_key(None) is None
    assert text.normalize_code("Résilié") == "RESILIE"
    assert text.normalize_code("en cours") == "EN_COURS"
    assert text.normalize_code(None) is None


# ------------------------------------------------------------------ identifiants
def test_canonical_id_is_deterministic_and_source_scoped():
    first = canonical_id("client", "AB", 42)
    assert first == canonical_id("client", "AB", "42")
    assert first != canonical_id("client", "AP", 42)  # pas de collision AB / AP
    assert first != canonical_id("contrat", "AB", 42)


@pytest.mark.parametrize("source, source_id", [("XX", 1), ("AB", None), ("AB", "  ")])
def test_canonical_id_rejects_invalid_input(source, source_id):
    with pytest.raises(ValueError):
        canonical_id("client", source, source_id)


# ------------------------------------------------------------------ sécurité
def test_pseudonymize_is_stable_and_keyed():
    token = security.pseudonymize("a@b.fr", "k1")
    assert token == security.pseudonymize("a@b.fr", "k1")
    assert token != security.pseudonymize("a@b.fr", "k2")
    assert len(token) == 64
    assert security.pseudonymize(None, "k1") is None


def test_encrypt_round_trip_and_random_nonce():
    key = "22" * 32
    first, second = security.encrypt("1234567890123", key), security.encrypt("1234567890123", key)
    assert first != second  # nonce aléatoire
    assert security.decrypt(first, key) == "1234567890123"
    assert security.encrypt(None, key) is None and security.decrypt(None, key) is None


def test_redact_free_text():
    redacted = security.redact_free_text(
        "Rappeler au 06 12 34 56 78 ou client.test@gmail.com, "
        "IBAN FR76 3000 4000 5000 6000 7000 891"
    )
    assert "[TEL]" in redacted and "[EMAIL]" in redacted and "[IBAN]" in redacted
    assert "06 12" not in redacted
    assert security.redact_free_text(None) is None


def test_redaction_regex_is_linear_on_adversarial_input():
    """Anti-ReDoS : une entrée hostile de 50 000 caractères est traitée instantanément."""
    import time

    hostile = "a" * 25_000 + "@" + "b." * 12_500
    start = time.perf_counter()
    security.redact_free_text(hostile)
    assert time.perf_counter() - start < 1.0
