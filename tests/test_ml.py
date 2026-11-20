"""Tests de l'évaluation, de l'entraînement et du service de prédiction (P7 / P8)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from unify.ml.evaluate import evaluate, fairness_by_group, lift_at, precision_recall_at, roc_points
from unify.ml.features import FEATURES, TARGET
from unify.ml.predict import Predictor, log_prediction, risk_level
from unify.ml.train import check_features, save, train


def test_lift_and_precision_on_perfect_ranking():
    y = np.array([1] * 10 + [0] * 90)
    scores = np.linspace(1, 0, 100)
    assert lift_at(y, scores, 0.10) == pytest.approx(10.0)
    assert precision_recall_at(y, scores, 0.10) == (1.0, 1.0)
    assert lift_at(np.zeros(10), np.ones(10)) == 0.0
    assert precision_recall_at(np.zeros(10), np.ones(10)) == (0.0, 0.0)


def test_evaluate_and_roc():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 200)
    metrics = evaluate(y, y * 0.6 + rng.random(200) * 0.4)
    assert metrics["auc"] > 0.9 and metrics["n"] == 200
    points = roc_points(y, rng.random(200), n_points=10)
    assert len(points) == 10 and points[0] == {"fpr": 0.0, "tpr": 0.0}


def test_fairness_by_group():
    df = pd.DataFrame(
        {"g": ["a"] * 4 + ["b"] * 2, "y": [0, 1, 0, 1, 0, 0], "s": [0.1, 0.9, 0.2, 0.8, 0.5, 0.95]}
    )
    rows = {r["groupe"]: r for r in fairness_by_group(df, "g", "y", "s")}
    assert rows["a"]["auc"] == 1.0 and rows["b"]["auc"] is None


@pytest.mark.parametrize("bad", ["date_fin", "score_fraude_source", "email_hash"])
def test_check_features_blocks_leakage(bad):
    with pytest.raises(ValueError, match="interdites"):
        check_features([*FEATURES, bad])


@pytest.fixture(scope="module")
def trained(gold_frame, tmp_path_factory):
    result = train(gold_frame)
    out = tmp_path_factory.mktemp("model")
    save(result, out)
    return result, out


def test_train_selects_champion_and_writes_card(trained):
    result, out = trained
    assert result.champion_name in {"logreg", "hgb"}
    assert set(result.card["comparaison"]) == {"logreg", "hgb"}
    assert result.metrics["auc"] > 0.7
    card = json.loads((out / "model_card.json").read_text(encoding="utf-8"))
    assert card["features"] == FEATURES and card["nom"].startswith("resiliation_")
    assert {"par_source", "par_tranche_age"} <= set(card["equite"])
    registry = json.loads((out / "registry.json").read_text())
    assert len(registry) == 1 and registry[0]["statut"] in {"CHAMPION", "REFUSE"}


def test_save_updates_registry_without_duplicates(trained):
    result, out = trained
    save(result, out)
    assert len(json.loads((out / "registry.json").read_text())) == 1


def test_predictor_scores_and_explains(trained, gold_frame):
    _, out = trained
    predictor = Predictor.load(out)
    risky = gold_frame.sort_values("nb_paiements_rejetes_12m").iloc[-1]
    result = predictor.predict_one(risky)
    assert 0 <= result["score"] <= 1
    assert result["niveau_risque"] == risk_level(result["score"])
    assert result["version_modele"] == predictor.version
    assert len(result["facteurs"]) <= 3
    assert all(f["contribution"] > 0 for f in result["facteurs"])
    contributions = [f["contribution"] for f in result["facteurs"]]
    assert contributions == sorted(contributions, reverse=True)


def test_predictor_rejects_missing_features(trained):
    predictor = Predictor.load(trained[1])
    with pytest.raises(ValueError, match="manquantes"):
        predictor.score(pd.DataFrame({"age": [30]}))


def test_risk_levels():
    assert (risk_level(0.8), risk_level(0.3), risk_level(0.01)) == ("ÉLEVÉ", "MOYEN", "FAIBLE")


def test_log_prediction_appends_json_lines(tmp_path):
    journal = tmp_path / "j" / "journal.jsonl"
    log_prediction(journal, "id-1", {"score": 0.4}, "user")
    log_prediction(journal, "id-2", {"score": 0.1}, "admin")
    lines = [json.loads(x) for x in journal.read_text().splitlines()]
    assert [x["id_client_uni"] for x in lines] == ["id-1", "id-2"]
    assert lines[0]["utilisateur"] == "user" and "horodatage_utc" in lines[0]


def test_target_column_is_binary(gold_frame):
    assert set(gold_frame[TARGET].unique()) <= {0, 1}


def test_save_refuses_directory_outside_allowed_roots(trained):
    result, _ = trained
    with pytest.raises(ValueError, match="non autorisé"):
        save(result, Path("/etc/unify"))


def test_registry_ignores_malformed_entries(trained):
    result, out = trained
    (out / "registry.json").write_text(
        json.dumps(
            [
                {"nom": "x"},
                "corrompu",
                {"nom": "ok", "date": "d", "auc": 0.8, "lift": 3.1, "statut": "REFUSE", "extra": 1},
            ]
        )
    )
    save(result, out)
    names = [r["nom"] for r in json.loads((out / "registry.json").read_text())]
    assert names == ["ok", result.card["nom"]]
    (out / "registry.json").write_text('{"pas": "une liste"}')
    save(result, out)
    assert len(json.loads((out / "registry.json").read_text())) == 1
