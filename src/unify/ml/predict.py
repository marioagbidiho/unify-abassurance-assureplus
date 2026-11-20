"""Processus P8 : service de prédiction (scoring + explication + journal des prédictions)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from unify.ml.features import FEATURE_LABELS

RISK_LEVELS = [(0.5, "ÉLEVÉ"), (0.25, "MOYEN"), (0.0, "FAIBLE")]


def risk_level(score: float) -> str:
    return next(label for threshold, label in RISK_LEVELS if score >= threshold)


@dataclass
class Predictor:
    model: object
    card: dict

    @classmethod
    def load(cls, model_dir: Path | str) -> Predictor:
        model_dir = Path(model_dir)
        card = json.loads((model_dir / "model_card.json").read_text(encoding="utf-8"))
        return cls(joblib.load(model_dir / "model.joblib"), card)

    @property
    def features(self) -> list[str]:
        return self.card["features"]

    @property
    def version(self) -> str:
        return self.card["nom"]

    def _matrix(self, rows: pd.DataFrame) -> pd.DataFrame:
        missing = [f for f in self.features if f not in rows.columns]
        if missing:
            raise ValueError(f"Features manquantes : {missing}")
        return rows[self.features].astype(float)

    def score(self, rows: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self._matrix(rows))[:, 1]

    def explain(self, row: pd.Series, top: int = 3) -> list[dict]:
        """Top des facteurs qui AUGMENTENT le risque (méthode par occlusion).

        Pour chaque feature, on remplace la valeur du client par la valeur de référence
        (médiane d'apprentissage) : la baisse de score obtenue est la contribution du facteur.
        """
        x = self._matrix(row.to_frame().T)
        base = float(self.model.predict_proba(x)[0, 1])
        reference = self.card["reference_explication"]
        variants = pd.concat([x] * len(self.features), ignore_index=True)
        for i, feature in enumerate(self.features):
            variants.loc[i, feature] = reference[feature]
        contributions = base - self.model.predict_proba(variants)[:, 1]
        order = np.argsort(-contributions)
        factors = []
        for i in order[:top]:
            if contributions[i] <= 0:
                break
            feature = self.features[i]
            value = x.iloc[0][feature]
            factors.append(
                {
                    "feature": feature,
                    "libelle": FEATURE_LABELS.get(feature, feature),
                    "valeur": None if pd.isna(value) else round(float(value), 2),
                    "reference": round(reference[feature], 2),
                    "contribution": round(float(contributions[i]), 4),
                }
            )
        return factors

    def predict_one(self, row: pd.Series) -> dict:
        score = float(self.score(row.to_frame().T)[0])
        return {
            "score": round(score, 4),
            "niveau_risque": risk_level(score),
            "facteurs": self.explain(row),
            "version_modele": self.version,
        }


def log_prediction(journal: Path, client_id: str, result: dict, user: str) -> None:
    """Journal des prédictions (D7) : une ligne JSON par décision, pour l'audit (AI Act)."""
    journal.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "horodatage_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "id_client_uni": client_id,
        "utilisateur": user,
        **result,
    }
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
