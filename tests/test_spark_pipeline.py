"""Test d'intégration de bout en bout.

sources -> Kafka -> BRONZE -> SILVER -> MDM -> GOLD -> modèle.

Démarre une session Spark locale (≈ 1 à 2 minutes). Marqueur : ``pytest -m spark``.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from unify.lake import Lake
from unify.ml.features import FEATURES, ID_COLUMNS, TARGET
from unify.pipeline import run
from unify.sources.generator import GeneratorConfig

pytestmark = pytest.mark.spark

CFG = GeneratorConfig(n_ab=400, n_ap=200, overlap=40, seed=11)


@pytest.fixture(scope="module")
def pipeline_run(tmp_path_factory):
    from unify.config import Settings

    tmp = tmp_path_factory.mktemp("e2e")
    settings = Settings(
        env="test",
        ab_source_url=f"sqlite:///{tmp}/ab.db",
        ap_source_url=f"sqlite:///{tmp}/ap.db",
        lake_root=str(tmp / "lake"),
        artifacts_dir=str(tmp / "artifacts"),
        crypto_key_hex="33" * 32,
    )
    report = run(settings, generator_cfg=CFG, with_spark_mllib=True)
    return settings, report


def test_every_source_row_is_traced(pipeline_run):
    _, report = pipeline_run
    assert sum(report["kafka"].values()) == sum(report["sources"].values())
    assert sum(report["bronze"].values()) == sum(report["sources"].values())
    for entity, q in report["qualite"].items():
        # réconciliation : lues = valides + quarantaine + doublons supprimés
        assert q["nb_lignes_lues"] == (
            q["nb_lignes_valides"] + q["nb_lignes_quarantaine"] + q["nb_doublons_supprimes"]
        ), entity
        assert q["taux_conformite"] >= 0.95, entity


def test_silver_has_canonical_tables_without_orphans(pipeline_run):
    settings, _ = pipeline_run
    lake = Lake(settings.lake_root)
    clients = Lake.read(lake.silver("client"))
    contrats = Lake.read(lake.silver("contrat"))
    paiements = Lake.read(lake.silver("paiement"))
    assert clients["id_client_uni"].is_unique and clients["id_personne_mdm"].notna().all()
    assert set(contrats["id_client_uni"]) <= set(clients["id_client_uni"])
    assert set(paiements["id_contrat_uni"]) <= set(contrats["id_contrat_uni"])
    assert {"AB", "AP"} == set(clients["source_systeme"])
    rejects = Lake.read(lake.path("quarantaine"))
    assert not rejects.empty and rejects["motif_rejet"].str.len().gt(0).all()


def test_mdm_finds_shared_customers(pipeline_run):
    _, report = pipeline_run
    assert 0.8 * CFG.overlap <= report["mdm"]["nb_doublons_inter_societes"] <= 1.1 * CFG.overlap


def test_gold_and_model_artifacts(pipeline_run):
    settings, report = pipeline_run
    gold = Lake.read(Lake(settings.lake_root).gold("ft_client_resiliation"))
    assert list(gold.columns) == [*ID_COLUMNS, *FEATURES, TARGET]
    assert gold["id_client_uni"].is_unique and gold[TARGET].isin([0, 1]).all()
    assert 0.02 < report["gold"]["taux_resiliation_90j"] < 0.4
    out = Path(settings.artifacts_dir)
    card = json.loads((out / "model_card.json").read_text(encoding="utf-8"))
    assert "spark_gbt" in card["comparaison"]
    scores = pd.read_parquet(out / "scores.parquet")
    assert scores["score"].is_monotonic_decreasing and len(scores) == len(gold)
    assert (out / "pipeline_report.json").exists()
