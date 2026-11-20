"""Orchestration de bout en bout du pipeline UNIFY (équivalent local du DAG Airflow).

P0  sources simulées (SQLite aux schémas Oracle / SQL Server de l'annexe)
P1  extraction + enveloppes CDC (Talend / Debezium)
P2  publication / consommation Kafka         topics {env}.{ab|ap}.raw.{entité}.v1
P3  écriture BRONZE (brut, immuable)          /data/bronze/{src}/{entité}/date_ingestion=
P3/P4 Spark : modèle canonique + qualité      /data/silver/uni_*  +  /data/quarantaine
P5  Spark : MDM (golden record)               /data/silver/mdm_personne
P6  Spark : features                          /data/gold/ft_client_resiliation
P7  entraînement (scikit-learn + Spark MLlib), registre de modèles
P8  scoring du portefeuille + export des artefacts pour l'API / l'application
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from unify.config import Settings, get_settings
from unify.ingestion.extract import ENTITY_OF_TABLE, SOURCE_TABLES, extract_table, to_cdc_events
from unify.lake import Lake
from unify.ml.features import FEATURES, ID_COLUMNS, TARGET
from unify.ml.predict import Predictor, risk_level
from unify.ml.train import save, train
from unify.sources.generator import GeneratorConfig, generate, write_sqlite
from unify.transport.bus import MessageBus, get_bus

log = logging.getLogger("unify.pipeline")
KEY_FIELDS = {
    "AB_CLIENT": "AB_CLIENT_ID",
    "AP_USERS": "AP_USER_ID",
    "AB_CONTRAT": "AB_POLICY_NUMBER",
    "AP_CONTRACTS": "AP_CONTRACT_REF",
    "AB_SINISTRE": "AB_CLAIM_ID",
    "AP_CLAIMS": "AP_SINISTRE_NUM",
    "AB_PAIEMENT": "AB_PAYMENT_ID",
    "AP_PAYMENTS": "AP_PAYMENT_REF",
}


def _sqlite_path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ValueError("La génération de données simulées n'est possible que vers SQLite")
    return Path(url.removeprefix("sqlite:///"))


def step_generate(settings: Settings, cfg: GeneratorConfig | None = None) -> dict:
    """P0 (DEV/CI uniquement) : crée les bases sources simulées aux schémas de l'annexe."""
    tables = generate(cfg)
    write_sqlite(tables, _sqlite_path(settings.ab_source_url), _sqlite_path(settings.ap_source_url))
    return {name: len(df) for name, df in tables.items()}


def step_extract_publish(
    settings: Settings, bus: MessageBus, redeliver: tuple[str, ...] = ()
) -> dict:
    """P1 + P2 : extraction des 8 tables et publication dans les topics Kafka.

    ``redeliver`` simule une re-livraison « at-least-once » (crash du consommateur avant
    le commit des offsets) : les événements des tables listées sont publiés deux fois.
    """
    counts = {}
    for source, tables in SOURCE_TABLES.items():
        url = settings.ab_source_url if source == "AB" else settings.ap_source_url
        for table in tables:
            topic = settings.topic(source.lower(), "raw", ENTITY_OF_TABLE[table])
            n = 0
            for chunk in extract_table(url, table):
                events = to_cdc_events(chunk, source, table)
                n += bus.publish(topic, events, key_field=KEY_FIELDS[table])
                if table in redeliver:
                    bus.publish(topic, events, key_field=KEY_FIELDS[table])
            counts[topic] = n
    return counts


def step_bronze(settings: Settings, bus: MessageBus, lake: Lake, day: date) -> dict:
    """P3 (partie BRONZE) : consommation des topics et écriture brute, colonnes d'origine."""
    counts = {}
    for source, tables in SOURCE_TABLES.items():
        for table in tables:
            entity = ENTITY_OF_TABLE[table]
            topic = settings.topic(source.lower(), "raw", entity)
            rows = [e["after"] for e in bus.consume(topic) if e.get("after")]
            if not rows:
                continue
            df = pd.DataFrame(rows).astype(object)
            df = df.map(
                lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
            )
            Lake.write(
                df, lake.bronze(source, entity, day), f"part-{int(time.time() * 1e6)}.parquet"
            )
            counts[f"{source}/{entity}"] = len(df)
    return counts


def score_portfolio(gold: pd.DataFrame, predictor: Predictor) -> pd.DataFrame:
    scored = gold[ID_COLUMNS + FEATURES + [TARGET]].copy()
    scored["score"] = predictor.score(scored).round(4)
    scored["niveau_risque"] = scored["score"].map(risk_level)
    return scored.sort_values("score", ascending=False).reset_index(drop=True)


def run(
    settings: Settings | None = None,
    clean: bool = True,
    redeliver: tuple[str, ...] = (),
    generator_cfg: GeneratorConfig | None = None,
    with_spark_mllib: bool = True,
) -> dict:
    """Exécute la chaîne complète et exporte les artefacts dans ``settings.artifacts_dir``."""
    from unify.spark_jobs import bronze_to_silver, features_job, mdm_job, train_job
    from unify.spark_jobs.session import get_spark

    settings = settings or get_settings()
    lake, day, started = Lake(settings.lake_root), date.today(), time.time()
    if clean:
        shutil.rmtree(Path(settings.lake_root), ignore_errors=True)
    report: dict = {"date_execution_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")}

    report["sources"] = step_generate(settings, generator_cfg)
    bus = get_bus(settings)
    report["kafka"] = step_extract_publish(settings, bus, redeliver)
    report["bronze"] = step_bronze(settings, bus, lake, day)

    spark = get_spark("unify-pipeline")
    report["qualite"] = bronze_to_silver.run(spark, lake, settings, day)
    report["mdm"] = mdm_job.run(spark, lake)
    report["gold"] = features_job.run(spark, lake, settings.snapshot_date, settings.horizon_days)
    gold_spark = spark.read.parquet(lake.gold("ft_client_resiliation"))
    gold = gold_spark.toPandas()

    result = train(gold)
    if with_spark_mllib:
        result.card["comparaison"]["spark_gbt"] = train_job.run(gold_spark)
    out = Path(settings.artifacts_dir)
    save(result, out)
    predictor = Predictor.load(out)
    score_portfolio(gold, predictor).to_parquet(out / "scores.parquet", index=False)
    report["modele"] = {
        "champion": result.card["nom"],
        **result.metrics,
        "accepte": result.card["accepte"],
    }
    report["duree_secondes"] = round(time.time() - started, 1)
    (out / "pipeline_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return report
