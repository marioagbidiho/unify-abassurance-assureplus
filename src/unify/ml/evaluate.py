"""Métriques d'évaluation du modèle de résiliation (critères d'acceptation du Bloc 1)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
    roc_curve,
)


def lift_at(y_true: np.ndarray, scores: np.ndarray, fraction: float = 0.10) -> float:
    """Lift du premier décile : taux de cible dans le top x % / taux de cible global."""
    y_true = np.asarray(y_true)
    base_rate = y_true.mean()
    if base_rate == 0:
        return 0.0
    n_top = max(int(len(y_true) * fraction), 1)
    top = np.argsort(-np.asarray(scores), kind="stable")[:n_top]
    return float(y_true[top].mean() / base_rate)


def precision_recall_at(y_true: np.ndarray, scores: np.ndarray, fraction: float = 0.10):
    y_true = np.asarray(y_true)
    n_top = max(int(len(y_true) * fraction), 1)
    top = np.argsort(-np.asarray(scores), kind="stable")[:n_top]
    hits = y_true[top].sum()
    positives = y_true.sum()
    return float(hits / n_top), float(hits / positives) if positives else 0.0


def evaluate(y_true, scores) -> dict:
    y_true = np.asarray(y_true).astype(int)
    scores = np.asarray(scores, dtype=float)
    precision, recall = precision_recall_at(y_true, scores)
    return {
        "auc": round(float(roc_auc_score(y_true, scores)), 4),
        "pr_auc": round(float(average_precision_score(y_true, scores)), 4),
        "lift_decile_1": round(lift_at(y_true, scores), 2),
        "precision_top10": round(precision, 4),
        "rappel_top10": round(recall, 4),
        "brier": round(float(brier_score_loss(y_true, scores)), 4),
        "taux_cible": round(float(y_true.mean()), 4),
        "n": int(len(y_true)),
    }


def roc_points(y_true, scores, n_points: int = 50) -> list[dict]:
    fpr, tpr, _ = roc_curve(y_true, scores)
    idx = np.linspace(0, len(fpr) - 1, min(n_points, len(fpr))).astype(int)
    return [{"fpr": round(float(fpr[i]), 4), "tpr": round(float(tpr[i]), 4)} for i in idx]


def fairness_by_group(df: pd.DataFrame, group_col: str, target: str, score_col: str) -> list[dict]:
    """AUC et taux de ciblage (top 10 %) par groupe : contrôle d'équité (ENF-10)."""
    threshold = df[score_col].quantile(0.90)
    rows = []
    for group, part in df.groupby(group_col, observed=True):
        has_both = part[target].nunique() == 2
        rows.append(
            {
                "groupe": str(group),
                "n": int(len(part)),
                "taux_cible": round(float(part[target].mean()), 4),
                "taux_cible_predit_top10": round(float((part[score_col] >= threshold).mean()), 4),
                "auc": round(float(roc_auc_score(part[target], part[score_col])), 4)
                if has_both
                else None,
            }
        )
    return rows
