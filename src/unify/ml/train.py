"""Processus P7 : entraînement, validation et enregistrement du modèle de résiliation.

Deux familles de modèles sont comparées sur le même découpage apprentissage / test :
* baseline : régression logistique (interprétable, sert de référence) ;
* challenger : Gradient Boosting (HistGradientBoostingClassifier, gère les NaN nativement).
Le modèle Spark MLlib (GBTClassifier) est entraîné par ``unify.spark_jobs.train_job`` sur
le cluster ; ses métriques sont ajoutées au même registre pour comparaison.

Le meilleur modèle (AUC test) devient « champion » s'il respecte les seuils d'acceptation
(AUC >= 0,75 et lift du 1er décile >= 3) ; il est sérialisé avec sa fiche (model card).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from unify.ml.evaluate import evaluate, fairness_by_group, roc_points
from unify.ml.features import FEATURES, FORBIDDEN_FEATURES, TARGET

ACCEPTANCE = {"auc": 0.75, "lift_decile_1": 3.0}
MODEL_NAME = "resiliation"
RANDOM_STATE = 42


@dataclass
class TrainingResult:
    champion_name: str
    model: object
    metrics: dict
    card: dict


def candidate_models() -> dict[str, object]:
    return {
        "logreg": make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=1000, C=0.5),
        ),
        "hgb": HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=300,
            max_leaf_nodes=15,
            min_samples_leaf=40,
            l2_regularization=1.0,
            early_stopping=True,
            validation_fraction=0.15,
            random_state=RANDOM_STATE,
        ),
    }


def check_features(features: list[str]) -> None:
    leaked = FORBIDDEN_FEATURES.intersection(features)
    if leaked:
        raise ValueError(f"Features interdites (fuite / données personnelles) : {sorted(leaked)}")


def train(gold: pd.DataFrame, features: list[str] | None = None) -> TrainingResult:
    features = features or FEATURES
    check_features(features)
    data = gold.dropna(subset=[TARGET]).copy()
    x, y = data[features].astype(float), data[TARGET].astype(int)
    x_train, x_test, y_train, y_test, _, idx_test = train_test_split(
        x, y, data.index, test_size=0.25, stratify=y, random_state=RANDOM_STATE
    )

    results = {}
    fitted = {}
    for name, model in candidate_models().items():
        model.fit(x_train, y_train)
        fitted[name] = model
        results[name] = evaluate(y_test, model.predict_proba(x_test)[:, 1])

    champion = max(results, key=lambda n: results[n]["auc"])
    model = fitted[champion]
    scores = model.predict_proba(x_test)[:, 1]
    test = data.loc[idx_test].assign(score=scores)
    test["tranche_age"] = pd.cut(
        test["age"], [0, 30, 45, 60, 120], labels=["<30", "30-44", "45-59", "60+"], right=False
    )
    accepted = all(results[champion][k] >= v for k, v in ACCEPTANCE.items())
    version = "v1.0"
    card = {
        "nom": f"{MODEL_NAME}_{champion}_{version}",
        "cas_usage": "Prédiction de la résiliation à 90 jours (MVP, EF-09)",
        "algorithme": type(model[-1] if hasattr(model, "steps") else model).__name__,
        "version": version,
        "date_entrainement_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "features": features,
        "reference_explication": {f: float(np.nanmedian(x_train[f])) for f in features},
        "nb_apprentissage": int(len(x_train)),
        "nb_test": int(len(x_test)),
        "metriques_test": results[champion],
        "comparaison": results,
        "seuils_acceptation": ACCEPTANCE,
        "accepte": accepted,
        "courbe_roc": roc_points(y_test, scores),
        "equite": {
            "par_source": fairness_by_group(test, "source_systeme", TARGET, "score"),
            "par_tranche_age": fairness_by_group(test, "tranche_age", TARGET, "score"),
        },
        "limites": [
            "Données synthétiques (échelle 1/2 000) : à ré-entraîner sur les données réelles.",
            "Validation par découpage aléatoire stratifié ; validation hors période à prévoir.",
            "Score d'aide à la décision : aucune décision automatique défavorable (RGPD art. 22).",
        ],
    }
    return TrainingResult(champion, model, results[champion], card)


def save(result: TrainingResult, out_dir: Path) -> Path:
    """Sérialise le modèle champion, sa fiche et met à jour le registre des modèles."""
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(result.model, out_dir / "model.joblib")
    (out_dir / "model_card.json").write_text(
        json.dumps(result.card, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    registry_path = out_dir / "registry.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else []
    registry = [r for r in registry if r["nom"] != result.card["nom"]]
    registry.append(
        {
            "nom": result.card["nom"],
            "date": result.card["date_entrainement_utc"],
            "auc": result.metrics["auc"],
            "lift": result.metrics["lift_decile_1"],
            "statut": "CHAMPION" if result.card["accepte"] else "REFUSE",
        }
    )
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_dir / "model.joblib"
