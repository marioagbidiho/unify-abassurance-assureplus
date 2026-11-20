"""Création de la session Spark et schémas des tables canoniques."""

from __future__ import annotations

import os

from pyspark.sql import SparkSession
from pyspark.sql import types as T

from unify.mapping.canonical import CANONICAL_COLUMNS

_TYPES = {
    "date_naissance": T.DateType(),
    "date_debut": T.DateType(),
    "date_fin": T.DateType(),
    "date_creation_utc": T.TimestampType(),
    "dt_ingestion_utc": T.TimestampType(),
    "date_heure_sinistre_utc": T.TimestampType(),
    "date_heure_paiement_utc": T.TimestampType(),
    "prime_annuelle_eur": T.DoubleType(),
    "montant_estime_eur": T.DoubleType(),
    "montant_eur": T.DoubleType(),
    "score_fraude_source": T.DoubleType(),
    "score_fidelite": T.IntegerType(),
}


def canonical_schema(entity: str, extra: tuple[str, ...] = ()) -> T.StructType:
    """Schéma Spark d'une table uni_* (toutes les colonnes non typées sont des chaînes)."""
    cols = [*CANONICAL_COLUMNS[entity], *extra]
    return T.StructType([T.StructField(c, _TYPES.get(c, T.StringType()), True) for c in cols])


def get_spark(app_name: str = "unify") -> SparkSession:
    """Session locale en DEV/CI ; en PRD, ``spark-submit --master yarn`` fournit le master.

    ``spark.sql.session.timeZone=UTC`` est indispensable : sans lui, les horodatages déjà
    convertis en UTC seraient décalés une seconde fois selon le fuseau de la machine.
    """
    builder = (
        SparkSession.builder.appName(app_name)
        .master(os.environ.get("SPARK_MASTER", "local[*]"))
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", os.environ.get("SPARK_SHUFFLE_PARTITIONS", "8"))
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.driver.memory", os.environ.get("SPARK_DRIVER_MEMORY", "2g"))
    )
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark
