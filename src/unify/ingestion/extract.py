"""Processus P1 - Capture & extraction des tables sources.

En production :
* le chargement initial est réalisé par les jobs Talend (talend/README.md) ;
* la synchronisation continue est assurée par Debezium (Kafka Connect) qui lit les journaux
  Oracle (LogMiner) et SQL Server (CDC) sans modifier les bases.

Ce module fournit l'équivalent Python utilisé en développement, en recette et dans la CI :
extraction par lots (``chunksize``) via SQLAlchemy, extraction incrémentale par
« watermark » et production d'événements au format d'enveloppe Debezium.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import date, datetime

import pandas as pd
from sqlalchemy import create_engine, text

SOURCE_TABLES = {
    "AB": ["AB_CLIENT", "AB_CONTRAT", "AB_SINISTRE", "AB_PAIEMENT"],
    "AP": ["AP_USERS", "AP_CONTRACTS", "AP_CLAIMS", "AP_PAYMENTS"],
}
ENTITY_OF_TABLE = {
    "AB_CLIENT": "client",
    "AP_USERS": "client",
    "AB_CONTRAT": "contrat",
    "AP_CONTRACTS": "contrat",
    "AB_SINISTRE": "sinistre",
    "AP_CLAIMS": "sinistre",
    "AB_PAIEMENT": "paiement",
    "AP_PAYMENTS": "paiement",
}
# Colonne de « watermark » pour l'extraction incrémentale (si la table en possède une)
WATERMARKS = {
    "AB_CLIENT": "AB_DATE_CREATION",
    "AP_USERS": "AP_CREATED_AT",
    "AB_PAIEMENT": "AB_DATE_PAIEMENT",
    "AP_PAYMENTS": "AP_PAYMENT_DATETIME",
}
_ALLOWED = {t for tables in SOURCE_TABLES.values() for t in tables}


def _check_table(table: str) -> None:
    # Protection contre l'injection SQL : seuls les noms de tables connus sont acceptés
    if table not in _ALLOWED:
        raise ValueError(f"Table source non autorisée : {table}")


def extract_table(
    url: str, table: str, chunksize: int = 50_000, since: datetime | None = None
) -> Iterator[pd.DataFrame]:
    """Lit une table source par lots. ``since`` active l'extraction incrémentale."""
    _check_table(table)
    # nom de table validé par liste blanche (_check_table) : pas d'injection possible
    query = f"SELECT * FROM {table}"  # noqa: S608  # nosec B608
    params: dict = {}
    if since is not None and table in WATERMARKS:
        query += f" WHERE {WATERMARKS[table]} > :since"
        params["since"] = since
    engine = create_engine(url)
    with engine.connect() as conn:
        yield from pd.read_sql_query(text(query), conn, params=params, chunksize=chunksize)
    engine.dispose()


def _jsonable(value: object) -> object:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):  # types numpy -> types Python natifs
        return value.item()
    return value


def to_cdc_events(df: pd.DataFrame, source: str, table: str, op: str = "r") -> list[dict]:
    """Convertit des lignes en événements au format d'enveloppe Debezium.

    ``op`` : r = lecture initiale (snapshot), c = création, u = modification, d = suppression.
    """
    ts_ms = int(time.time() * 1000)
    events = []
    for row in df.to_dict("records"):
        after = {k: _jsonable(v) for k, v in row.items()}
        events.append(
            {
                "before": None,
                "after": None if op == "d" else after,
                "op": op,
                "ts_ms": ts_ms,
                "source": {
                    "system": source,
                    "table": table,
                    "connector": "oracle" if source == "AB" else "sqlserver",
                },
            }
        )
    return events
