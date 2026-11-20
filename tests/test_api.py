"""Tests de l'API REST : authentification, rôles, scoring, journalisation."""

import pytest
from fastapi.testclient import TestClient

from unify.api import main as api
from unify.api.security import authenticate, create_token, decode_token, users
from unify.ml.train import save, train
from unify.pipeline import score_portfolio


@pytest.fixture(scope="module")
def client(gold_frame, tmp_path_factory, monkeypatch_module):
    out = tmp_path_factory.mktemp("artifacts")
    save(train(gold_frame), out)
    from unify.ml.predict import Predictor

    score_portfolio(gold_frame, Predictor.load(out)).to_parquet(out / "scores.parquet")
    (out / "pipeline_report.json").write_text('{"qualite": {}}', encoding="utf-8")
    monkeypatch_module.setenv("UNIFY_ARTIFACTS", str(out))
    for cached in (api.artifacts_dir, api.predictor, api.portfolio):
        cached.cache_clear()
    yield TestClient(api.app)
    for cached in (api.artifacts_dir, api.predictor, api.portfolio):
        cached.cache_clear()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _token(client, username, password):
    resp = client.post("/auth/token", data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_health(client):
    body = client.get("/health").json()
    assert body["statut"] == "ok" and body["modele"].startswith("resiliation_")


def test_login_failure(client):
    assert (
        client.post("/auth/token", data={"username": "admin", "password": "x"}).status_code == 401
    )
    assert (
        client.post("/auth/token", data={"username": "nobody", "password": "x"}).status_code == 401
    )


def test_routes_require_token(client):
    assert client.get("/v1/resiliation/clients").status_code == 401
    bad = {"Authorization": "Bearer pas-un-jeton"}
    assert client.get("/v1/resiliation/clients", headers=bad).status_code == 401


def test_user_can_score_but_not_administer(client):
    headers = _token(client, "user", "User#2026!")
    top = client.get("/v1/resiliation/clients?n=5", headers=headers).json()
    assert len(top) == 5 and top[0]["score"] >= top[-1]["score"]
    assert set(top[0]) == {"id_client_uni", "source_systeme", "score", "niveau_risque"}
    only_ap = client.get("/v1/resiliation/clients?n=3&source=AP", headers=headers).json()
    assert {r["source_systeme"] for r in only_ap} == {"AP"}
    detail = client.get(
        f"/v1/resiliation/clients/{top[0]['id_client_uni']}", headers=headers
    ).json()
    assert detail["score"] == pytest.approx(top[0]["score"], abs=1e-4)
    assert client.get("/v1/resiliation/clients/inconnu", headers=headers).status_code == 404
    assert client.get("/admin/qualite", headers=headers).status_code == 403
    assert client.get("/v1/resiliation/modele", headers=headers).json()["accepte"] in (True, False)


def test_score_endpoint_validates_input(client):
    headers = _token(client, "user", "User#2026!")
    payload = {
        "age": 30,
        "anciennete_mois": 12,
        "prime_annuelle_totale": 800,
        "nb_paiements_rejetes_12m": 3,
    }
    body = client.post("/v1/resiliation/score", json=payload, headers=headers).json()
    assert 0 <= body["score"] <= 1 and "facteurs" in body
    invalid = client.post("/v1/resiliation/score", json={**payload, "age": 5}, headers=headers)
    assert invalid.status_code == 422


def test_admin_sees_quality_and_journal(client):
    headers = _token(client, "admin", "Admin#2026!")
    assert client.get("/admin/qualite", headers=headers).json() == {"qualite": {}}
    journal = client.get("/admin/journal?n=10", headers=headers).json()
    assert journal and journal[-1]["utilisateur"] == "user"


def test_journal_empty_when_no_prediction(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "artifacts_dir", lambda: tmp_path)
    headers = _token(client, "admin", "Admin#2026!")
    assert client.get("/admin/journal", headers=headers).json() == []


def test_security_helpers():
    user = authenticate("admin", "Admin#2026!")
    assert user is not None and user.role == "admin"
    assert authenticate("admin", "wrong") is None
    claims = decode_token(create_token(user))
    assert claims["sub"] == "admin" and claims["role"] == "admin"
    assert set(users()) == {"admin", "user"}
