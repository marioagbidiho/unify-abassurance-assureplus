"""Processus P6 : feature engineering SILVER -> GOLD (gold.ft_client_resiliation).

Population : clients ayant au moins un contrat actif à la date d'observation T.
Cible      : 1 si au moins un contrat du client est résilié dans ]T, T + horizon].
"""

from __future__ import annotations

from datetime import date, timedelta

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from unify.lake import Lake
from unify.ml.features import FEATURES, ID_COLUMNS, TARGET


def build_features(
    clients: DataFrame,
    contrats: DataFrame,
    sinistres: DataFrame,
    paiements: DataFrame,
    personnes: DataFrame,
    snapshot: date,
    horizon_days: int,
) -> DataFrame:
    t = F.lit(snapshot.isoformat()).cast("date")
    t_end = F.lit(f"{(snapshot + timedelta(days=1)).isoformat()} 00:00:00").cast("timestamp")
    t_start_12m = F.lit(f"{(snapshot - timedelta(days=365)).isoformat()} 00:00:00").cast(
        "timestamp"
    )
    horizon = F.lit((snapshot + timedelta(days=horizon_days)).isoformat()).cast("date")

    actifs = contrats.filter(
        (F.col("date_debut") <= t) & (F.col("date_fin").isNull() | (F.col("date_fin") > t))
    )
    portefeuille = actifs.groupBy("id_client_uni").agg(
        F.count("*").alias("nb_contrats_actifs"),
        F.countDistinct("code_produit_uni").alias("nb_produits_distincts"),
        F.round(F.sum("prime_annuelle_eur"), 2).alias("prime_annuelle_totale"),
        *[
            F.max((F.col("code_produit_uni") == code).cast("int")).alias(name)
            for code, name in (
                ("AUTO", "a_produit_auto"),
                ("HABITATION", "a_produit_habitation"),
                ("SANTE", "a_produit_sante"),
            )
        ],
    )

    cible = (
        contrats.filter(
            (F.col("statut_contrat") == "RESILIE")
            & (F.col("date_fin") > t)
            & (F.col("date_fin") <= horizon)
        )
        .select("id_client_uni")
        .distinct()
        .withColumn(TARGET, F.lit(1))
    )

    contrat_client = contrats.select("id_contrat_uni", "id_client_uni")
    sin_12m = (
        sinistres.join(contrat_client, "id_contrat_uni", "inner")
        .filter(
            (F.col("date_heure_sinistre_utc") >= t_start_12m)
            & (F.col("date_heure_sinistre_utc") < t_end)
        )
        .groupBy("id_client_uni")
        .agg(
            F.count("*").alias("nb_sinistres_12m"),
            F.round(F.sum("montant_estime_eur"), 2).alias("montant_sinistres_12m"),
            F.sum((F.col("statut_sinistre") == "REFUSE").cast("int")).alias(
                "nb_sinistres_refuses_12m"
            ),
        )
    )

    pay = paiements.join(contrat_client, "id_contrat_uni", "inner").filter(
        F.col("date_heure_paiement_utc") < t_end
    )
    pay_12m = (
        pay.filter(F.col("date_heure_paiement_utc") >= t_start_12m)
        .groupBy("id_client_uni")
        .agg(
            F.count("*").alias("nb_paiements_12m"),
            F.sum((F.col("statut_transaction") == "REJETE").cast("int")).alias(
                "nb_paiements_rejetes_12m"
            ),
            F.sum(
                F.when(F.col("statut_transaction") != "REJETE", F.col("montant_eur")).otherwise(0)
            ).alias("_montant_paye_12m"),
        )
    )
    last_pay = pay.groupBy("id_client_uni").agg(
        F.datediff(t, F.max(F.to_date("date_heure_paiement_utc"))).alias(
            "jours_depuis_dernier_paiement"
        )
    )

    base = (
        clients.join(portefeuille, "id_client_uni", "inner")
        .join(personnes.select("id_personne_mdm", "nb_comptes_lies"), "id_personne_mdm", "left")
        .join(sin_12m, "id_client_uni", "left")
        .join(pay_12m, "id_client_uni", "left")
        .join(last_pay, "id_client_uni", "left")
        .join(cible, "id_client_uni", "left")
    )

    return (
        base.withColumns(
            {
                "date_observation": t,
                "source_ap": (F.col("source_systeme") == "AP").cast("int"),
                "age": F.floor(F.months_between(t, F.col("date_naissance")) / 12).cast("int"),
                "anciennete_mois": F.floor(F.months_between(t, F.col("date_creation_utc"))).cast(
                    "int"
                ),
                "taux_paiement_12m": F.round(
                    F.coalesce(F.col("_montant_paye_12m"), F.lit(0.0))
                    / F.col("prime_annuelle_totale"),
                    3,
                ),
                "fidelite_renseignee": F.col("score_fidelite").isNotNull().cast("int"),
            }
        )
        .fillna(
            0,
            subset=[
                "nb_sinistres_12m",
                "montant_sinistres_12m",
                "nb_sinistres_refuses_12m",
                "nb_paiements_12m",
                "nb_paiements_rejetes_12m",
                TARGET,
            ],
        )
        .fillna(1, subset=["nb_comptes_lies"])
        .fillna(999, subset=["jours_depuis_dernier_paiement"])
        .select(*ID_COLUMNS, *FEATURES, TARGET)
    )


def run(spark: SparkSession, lake: Lake, snapshot: date, horizon_days: int) -> dict:
    gold = build_features(
        spark.read.parquet(lake.silver("client")),
        spark.read.parquet(lake.silver("contrat")),
        spark.read.parquet(lake.silver("sinistre")),
        spark.read.parquet(lake.silver("paiement")),
        spark.read.parquet(lake.mdm()),
        snapshot,
        horizon_days,
    ).cache()
    gold.write.mode("overwrite").parquet(lake.gold("ft_client_resiliation"))
    stats = gold.agg(F.count("*").alias("n"), F.avg(TARGET).alias("taux")).first()
    gold.unpersist()
    return {
        "nb_clients_observes": int(stats["n"]),
        "taux_resiliation_90j": round(float(stats["taux"] or 0), 4),
        "date_observation": snapshot.isoformat(),
        "horizon_jours": horizon_days,
    }
