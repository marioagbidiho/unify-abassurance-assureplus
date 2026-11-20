"""Processus P3 + P4 : standardisation BRONZE -> SILVER et contrôle qualité (Spark).

Pour chaque entité (client, contrat, sinistre, paiement), dans l'ordre des dépendances :

1. lecture des partitions BRONZE des deux sources ;
2. application des règles de mapping (``unify.mapping.canonical``) via ``mapInPandas`` :
   une seule implémentation des règles, testée unitairement, exécutée en parallèle ;
3. union AB + AP, dédoublonnage par clé (fenêtre ``row_number`` sur dt_ingestion_utc) ;
4. règles qualité ligne à ligne (``unify.quality.evaluate_rules``) puis contrôle
   d'intégrité référentielle par jointure avec la table SILVER parente ;
5. écriture SILVER (lignes conformes) et QUARANTAINE (rejets + motif).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from functools import reduce
from pathlib import Path

import pandas as pd
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from unify.config import Settings
from unify.lake import Lake
from unify.mapping.canonical import CANONICAL_COLUMNS, MAPPERS
from unify.quality.rules import KEYS, PARENT_KEYS, build_report, evaluate_rules, rules_for
from unify.spark_jobs.session import canonical_schema

ENTITY_ORDER = ["client", "contrat", "sinistre", "paiement"]


def _map_partitions(mapper: Callable, settings: Settings, columns: list[str]):
    def run(batches: Iterator[pd.DataFrame]) -> Iterator[pd.DataFrame]:
        for pdf in batches:
            yield mapper(pdf, settings)[columns]

    return run


def _flag_partitions(entity: str, columns: list[str]):
    def run(batches: Iterator[pd.DataFrame]) -> Iterator[pd.DataFrame]:
        for pdf in batches:
            reasons, _ = evaluate_rules(pdf, entity)
            yield pdf.assign(motif_rejet=reasons.values)[columns]

    return run


def _exists(location: str) -> bool:
    return location.startswith("hdfs://") or Path(location).exists()


def read_canonical(
    spark: SparkSession, lake: Lake, settings: Settings, entity: str
) -> DataFrame | None:
    """Lit les BRONZE AB et AP d'une entité et les traduit dans le modèle canonique."""
    schema = canonical_schema(entity)
    frames = []
    for table, (ent, mapper) in MAPPERS.items():
        location = lake.bronze(table[:2], entity)
        if ent != entity or not _exists(location):
            continue
        bronze = spark.read.parquet(location)
        bronze = bronze.select(*[c for c in bronze.columns if c != "date_ingestion"])
        frames.append(bronze.mapInPandas(_map_partitions(mapper, settings, schema.names), schema))
    if not frames:
        return None
    return reduce(DataFrame.unionByName, frames)


def standardize_entity(
    spark: SparkSession, lake: Lake, settings: Settings, entity: str, day: date
) -> dict:
    canonical = read_canonical(spark, lake, settings, entity)
    if canonical is None:
        return build_report(entity, 0, 0, 0, 0, {})
    canonical = canonical.cache()
    key = KEYS[entity]
    nb_lues = canonical.count()

    window = Window.partitionBy(key).orderBy(F.col("dt_ingestion_utc").desc())
    deduped = (
        canonical.withColumn("_rang", F.row_number().over(window)).filter("_rang = 1").drop("_rang")
    )
    nb_doublons = nb_lues - deduped.count()

    flagged_schema = canonical_schema(entity, extra=("motif_rejet",))
    flagged = deduped.mapInPandas(_flag_partitions(entity, flagged_schema.names), flagged_schema)

    if entity in PARENT_KEYS:
        parent_key = PARENT_KEYS[entity]
        parent_entity = {"id_client_uni": "client", "id_contrat_uni": "contrat"}[parent_key]
        parents = (
            spark.read.parquet(lake.silver(parent_entity))
            .select(parent_key)
            .distinct()
            .withColumn("_parent_ok", F.lit(True))
        )
        orphan = F.col("_parent_ok").isNull() & F.col(parent_key).isNotNull()
        flagged = (
            flagged.join(parents, on=parent_key, how="left")
            .withColumn(
                "motif_rejet",
                F.when(
                    orphan,
                    F.concat_ws(
                        "|",
                        F.when(F.col("motif_rejet") != "", F.col("motif_rejet")),
                        F.lit("INTEGRITE_REFERENTIELLE"),
                    ),
                ).otherwise(F.col("motif_rejet")),
            )
            .drop("_parent_ok")
        )

    flagged = flagged.cache()
    columns = CANONICAL_COLUMNS[entity]
    valid = flagged.filter(F.col("motif_rejet") == "").select(*columns)
    rejects = flagged.filter(F.col("motif_rejet") != "")
    valid.write.mode("overwrite").parquet(lake.silver(entity))
    rejects.write.mode("overwrite").parquet(lake.quarantine(entity, day))

    counts = _rule_counts(flagged, entity)
    nb_valides, nb_rejets = valid.count(), rejects.count()
    canonical.unpersist()
    flagged.unpersist()
    return build_report(entity, nb_lues, nb_doublons, nb_valides, nb_rejets, counts)


def _rule_counts(flagged: DataFrame, entity: str) -> dict[str, dict]:
    """Lignes en échec par règle (critiques : motif_rejet ; avertissements : anomalies)."""

    def explode_counts(column: str) -> dict[str, int]:
        rows = (
            flagged.select(
                F.explode(F.split(F.coalesce(F.col(column), F.lit("")), r"\|")).alias("code")
            )
            .filter(F.col("code") != "")
            .groupBy("code")
            .count()
            .collect()
        )
        return {r["code"]: int(r["count"]) for r in rows}

    critical = explode_counts("motif_rejet")
    anomalies = explode_counts("anomalies")
    counts = {}
    rules = {r.name: r for r in rules_for(entity)}
    for name, rule in rules.items():
        nb = critical.get(name, 0) if rule.severity == "CRITIQUE" else anomalies.get(name, 0)
        counts[name] = {"severite": rule.severity, "description": rule.description, "nb_lignes": nb}
    if entity in PARENT_KEYS:
        counts["INTEGRITE_REFERENTIELLE"] = {
            "severite": "CRITIQUE",
            "description": f"{PARENT_KEYS[entity]} inexistant",
            "nb_lignes": critical.get("INTEGRITE_REFERENTIELLE", 0),
        }
    return counts


def run(spark: SparkSession, lake: Lake, settings: Settings, day: date) -> dict[str, dict]:
    """Exécute P3/P4 pour toutes les entités et renvoie le rapport qualité."""
    return {
        entity: standardize_entity(spark, lake, settings, entity, day) for entity in ENTITY_ORDER
    }


__all__ = ["ENTITY_ORDER", "read_canonical", "run", "standardize_entity"]
