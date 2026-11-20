"""Fixtures partagées par les tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from unify.config import Settings
from unify.ml.features import FEATURES, TARGET
from unify.sources.generator import GeneratorConfig, generate


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        env="test",
        ab_source_url=f"sqlite:///{tmp_path}/ab.db",
        ap_source_url=f"sqlite:///{tmp_path}/ap.db",
        lake_root=str(tmp_path / "lake"),
        artifacts_dir=str(tmp_path / "artifacts"),
        pseudo_key="test-pepper",
        crypto_key_hex="11" * 32,
    )


@pytest.fixture(scope="session")
def small_tables() -> dict[str, pd.DataFrame]:
    """Petit jeu de données sources (déterministe) partagé par les tests."""
    return generate(GeneratorConfig(n_ab=120, n_ap=60, overlap=15, seed=7))


def make_gold(n: int = 600, seed: int = 0) -> pd.DataFrame:
    """Table GOLD synthétique avec un signal simple, pour tester l'entraînement sans Spark."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({f: rng.integers(0, 5, n).astype(float) for f in FEATURES})
    df["age"] = rng.integers(18, 85, n)
    df["prime_annuelle_totale"] = rng.uniform(200, 2000, n)
    df["score_fidelite"] = np.where(rng.random(n) < 0.5, np.nan, rng.uniform(0, 100, n))
    logit = -2 + 1.2 * df["nb_paiements_rejetes_12m"] - 0.03 * (df["age"] - 45)
    df[TARGET] = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    df["id_client_uni"] = [f"id-{i}" for i in range(n)]
    df["id_personne_mdm"] = df["id_client_uni"]
    df["source_systeme"] = np.where(df["source_ap"] > 2, "AP", "AB")
    df["date_observation"] = pd.Timestamp("2026-06-30")
    return df


@pytest.fixture(scope="session")
def gold_frame() -> pd.DataFrame:
    return make_gold()
