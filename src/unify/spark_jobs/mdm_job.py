"""Processus P5 : consolidation client (MDM) distribuée.

Les trois règles de rapprochement exigent toutes la même date de naissance : la date de
naissance est donc une clé de « blocking » exacte. Spark regroupe les clients par date de
naissance (``groupBy().applyInPandas``) et chaque groupe est traité par
``unify.mdm.build_golden_records`` : on évite le produit cartésien 17 M x 17 M.
"""

from __future__ import annotations

import pandas as pd
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from unify.lake import Lake
from unify.mdm.matching import build_golden_records

_OUT_SCHEMA = T.StructType(
    [
        T.StructField("id_client_uni", T.StringType()),
        T.StructField("id_personne_mdm", T.StringType()),
        T.StructField("regle_rapprochement", T.StringType()),
        T.StructField("score_confiance", T.DoubleType()),
        T.StructField("nb_comptes_lies", T.IntegerType()),
    ]
)
_INPUT = [
    "id_client_uni",
    "source_systeme",
    "nom",
    "prenom",
    "date_naissance",
    "email_hash",
    "code_postal",
]


def _match_block(pdf: pd.DataFrame) -> pd.DataFrame:
    personnes, enriched = build_golden_records(pdf)
    out = enriched[["id_client_uni", "id_personne_mdm"]].merge(personnes, on="id_personne_mdm")
    out["nb_comptes_lies"] = out["nb_comptes_lies"].astype("int32")
    return out[_OUT_SCHEMA.names]


def run(spark: SparkSession, lake: Lake) -> dict:
    clients = spark.read.parquet(lake.silver("client"))
    links = (
        clients.select(*_INPUT)
        .groupBy("date_naissance")
        .applyInPandas(_match_block, schema=_OUT_SCHEMA)
    ).cache()

    personnes = links.select(
        "id_personne_mdm", "regle_rapprochement", "score_confiance", "nb_comptes_lies"
    ).dropDuplicates(["id_personne_mdm"])
    personnes.write.mode("overwrite").parquet(lake.mdm())

    enriched = (
        clients.drop("id_personne_mdm")
        .join(links.select("id_client_uni", "id_personne_mdm"), "id_client_uni", "left")
        .localCheckpoint(eager=True)
    )
    enriched.write.mode("overwrite").parquet(lake.silver("client"))

    stats = (
        personnes.groupBy("regle_rapprochement")
        .agg(F.count("*").alias("nb_personnes"), F.sum("nb_comptes_lies").alias("nb_comptes"))
        .collect()
    )
    report = {
        "nb_clients": clients.count(),
        "nb_personnes": personnes.count(),
        "par_regle": {
            r["regle_rapprochement"]: {
                "nb_personnes": int(r["nb_personnes"]),
                "nb_comptes": int(r["nb_comptes"]),
            }
            for r in stats
        },
    }
    report["nb_doublons_inter_societes"] = report["nb_clients"] - report["nb_personnes"]
    links.unpersist()
    return report
