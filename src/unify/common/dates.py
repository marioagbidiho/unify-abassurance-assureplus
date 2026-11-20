"""Conversion et contrôle des dates hétérogènes des deux SI.

* AssurePlus stocke des dates en texte : AP_BIRTH_DATE VARCHAR(10) « YYYY-MM-DD »
  et AP_INCIDENT_DATE VARCHAR(19) « YYYY-MM-DD HH:MM:SS ».
* Les horodatages des deux SI sont en heure locale (Europe/Paris) sans fuseau :
  ils sont convertis en UTC dans le modèle canonique (colonnes *_utc).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d")


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return value is pd.NaT or (isinstance(value, str) and value.strip() in {"", "NULL", "null"})


def parse_date(value: object) -> date | None:
    """Convertit une date (objet date/datetime ou texte) ; None si absente ou invalide."""
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(value: object) -> datetime | None:
    """Convertit un horodatage (objet ou texte VARCHAR(19)) en datetime naïf ; None si invalide."""
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def to_utc(value: datetime | None, source_tz: str = "Europe/Paris") -> datetime | None:
    """Interprète un horodatage naïf dans le fuseau de la source et le convertit en UTC naïf."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(source_tz))
    return value.astimezone(UTC).replace(tzinfo=None)


def age_at(birth: date | None, ref: date) -> int | None:
    """Âge révolu à la date de référence."""
    if birth is None:
        return None
    years = ref.year - birth.year
    if (ref.month, ref.day) < (birth.month, birth.day):
        years -= 1
    return years
