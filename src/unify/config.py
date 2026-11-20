"""Configuration centralisée (12-factor : tout est surchargeable par variable d'environnement)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    """Paramètres d'exécution du pipeline."""

    env: str = field(default_factory=lambda: _env("UNIFY_ENV", "dev"))
    # Sources (SQLAlchemy URL). En PRD : oracle+oracledb://... et mssql+pyodbc://...
    ab_source_url: str = field(
        default_factory=lambda: _env(
            "UNIFY_AB_URL", f"sqlite:///{PROJECT_ROOT}/data/sources/ab_oracle.db"
        )
    )
    ap_source_url: str = field(
        default_factory=lambda: _env(
            "UNIFY_AP_URL", f"sqlite:///{PROJECT_ROOT}/data/sources/ap_sqlserver.db"
        )
    )
    # Transport : "memory" (tests / démo) ou "kafka"
    bus: str = field(default_factory=lambda: _env("UNIFY_BUS", "memory"))
    kafka_bootstrap: str = field(default_factory=lambda: _env("KAFKA_BOOTSTRAP", "localhost:9092"))
    # Data lake : chemin local (démo) ou hdfs://namenode:8020
    lake_root: str = field(default_factory=lambda: _env("UNIFY_LAKE", str(PROJECT_ROOT / "lake")))
    # Artefacts exposés (modèle, rapports) lus par l'API et l'application
    artifacts_dir: str = field(
        default_factory=lambda: _env("UNIFY_ARTIFACTS", str(PROJECT_ROOT / "data" / "demo"))
    )
    # Date d'observation (snapshot) et horizon de prédiction de la résiliation
    snapshot_date: date = field(
        default_factory=lambda: date.fromisoformat(_env("UNIFY_SNAPSHOT", "2026-06-30"))
    )
    horizon_days: int = field(default_factory=lambda: int(_env("UNIFY_HORIZON", "90")))
    # Clé de pseudonymisation (HMAC) et de chiffrement (AES-256-GCM, 32 octets hex)
    # Les valeurs par défaut ne servent qu'en DEV ; en PRD elles viennent du KMS / Vault.
    pseudo_key: str = field(default_factory=lambda: _env("UNIFY_PSEUDO_KEY", "dev-only-pepper"))
    crypto_key_hex: str = field(default_factory=lambda: _env("UNIFY_CRYPTO_KEY", "00" * 32))
    source_timezone: str = field(default_factory=lambda: _env("UNIFY_SOURCE_TZ", "Europe/Paris"))

    def topic(self, source: str, layer: str, entity: str, version: int = 1) -> str:
        """Nomenclature Bloc 1 : {env}.{source}.{couche}.{entité}.v{n}."""
        return f"{self.env}.{source}.{layer}.{entity}.v{version}"


def get_settings() -> Settings:
    return Settings()
