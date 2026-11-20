"""Processus P7 (version distribuée) : entraînement Spark MLlib sur la table GOLD.

Pipeline MLlib : Imputer (médiane) -> VectorAssembler -> GBTClassifier, évalué sur le même
type de découpage que le modèle scikit-learn. Sur le volume réel (17 M de clients, plusieurs
années d'historique), c'est ce job qui s'exécute sur YARN ; le modèle scikit-learn sert
d'implémentation légère pour l'API temps réel (latence < 300 ms sans JVM).
"""

from __future__ import annotations

from pyspark.ml import Pipeline
from pyspark.ml.classification import GBTClassifier
from pyspark.ml.feature import Imputer, VectorAssembler
from pyspark.ml.functions import vector_to_array
from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from unify.ml.evaluate import evaluate
from unify.ml.features import FEATURES, TARGET


def build_pipeline(features: list[str] = FEATURES) -> Pipeline:
    imputed = [f"{f}_imp" for f in features]
    return Pipeline(
        stages=[
            Imputer(inputCols=features, outputCols=imputed, strategy="median"),
            VectorAssembler(inputCols=imputed, outputCol="features_vec"),
            GBTClassifier(
                featuresCol="features_vec",
                labelCol=TARGET,
                maxIter=60,
                maxDepth=4,
                stepSize=0.1,
                seed=42,
            ),
        ]
    )


def run(gold: DataFrame) -> dict:
    data = gold.select(*[F.col(f).cast("double") for f in FEATURES], F.col(TARGET).cast("double"))
    train, test = data.randomSplit([0.75, 0.25], seed=42)
    model = build_pipeline().fit(train)
    preds = (
        model.transform(test)
        .select(F.col(TARGET), vector_to_array("probability")[1].alias("score"))
        .toPandas()
    )
    metrics = evaluate(preds[TARGET].values, preds["score"].values)
    metrics["algorithme"] = "Spark MLlib GBTClassifier"
    return metrics
