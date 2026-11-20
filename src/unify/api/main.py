"""API REST de prédiction de la résiliation (processus P8, flux F18).

Nomenclature Bloc 1 : /v{n}/{cas_usage}/{action}. Documentation OpenAPI : /docs.
Rôles : ``user`` (conseiller : consulter et scorer) ; ``admin`` (+ qualité, journal, modèle).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import jwt
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from unify import __version__
from unify.api.security import authenticate, create_token, decode_token
from unify.config import get_settings
from unify.ml.predict import Predictor, log_prediction

app = FastAPI(
    title="UNIFY - API de prédiction de résiliation",
    version=__version__,
    description="AbAssurance / AssurePlus - Bloc 3 BCD&IA - AGBIDIHO Mario",
)
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")


# ------------------------------------------------------------------ dépendances
@lru_cache(maxsize=1)
def artifacts_dir() -> Path:
    return Path(get_settings().artifacts_dir)


@lru_cache(maxsize=1)
def predictor() -> Predictor:
    return Predictor.load(artifacts_dir())


@lru_cache(maxsize=1)
def portfolio() -> pd.DataFrame:
    return pd.read_parquet(artifacts_dir() / "scores.parquet").set_index("id_client_uni")


def current_user(token: Annotated[str, Depends(oauth2)]) -> dict:
    try:
        return decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Jeton invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def admin_user(user: Annotated[dict, Depends(current_user)]) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé au rôle admin")
    return user


# ------------------------------------------------------------------ schémas
class ClientFeatures(BaseModel):
    source_ap: int = Field(0, ge=0, le=1)
    age: int = Field(..., ge=16, le=120)
    anciennete_mois: int = Field(..., ge=0)
    nb_contrats_actifs: int = Field(1, ge=1)
    nb_produits_distincts: int = Field(1, ge=1)
    prime_annuelle_totale: float = Field(..., gt=0)
    a_produit_auto: int = Field(0, ge=0, le=1)
    a_produit_habitation: int = Field(0, ge=0, le=1)
    a_produit_sante: int = Field(0, ge=0, le=1)
    nb_sinistres_12m: int = Field(0, ge=0)
    montant_sinistres_12m: float = Field(0, ge=0)
    nb_sinistres_refuses_12m: int = Field(0, ge=0)
    nb_paiements_12m: int = Field(0, ge=0)
    nb_paiements_rejetes_12m: int = Field(0, ge=0)
    taux_paiement_12m: float = Field(1.0, ge=0)
    jours_depuis_dernier_paiement: int = Field(30, ge=0)
    score_fidelite: float | None = Field(None, ge=0, le=100)
    fidelite_renseignee: int = Field(0, ge=0, le=1)
    nb_comptes_lies: int = Field(1, ge=1)


# ------------------------------------------------------------------ routes
@app.get("/health", tags=["exploitation"])
def health() -> dict:
    return {"statut": "ok", "version_api": __version__, "modele": predictor().version}


@app.post("/auth/token", tags=["authentification"])
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]) -> dict:
    user = authenticate(form.username, form.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants incorrects")
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role}


@app.get("/v1/resiliation/modele", tags=["résiliation"])
def model_info(user: Annotated[dict, Depends(current_user)]) -> dict:
    card = predictor().card
    return {
        k: card[k]
        for k in (
            "nom",
            "algorithme",
            "version",
            "date_entrainement_utc",
            "metriques_test",
            "seuils_acceptation",
            "accepte",
            "features",
        )
    }


@app.get("/v1/resiliation/clients", tags=["résiliation"])
def top_clients(
    user: Annotated[dict, Depends(current_user)],
    n: Annotated[int, Query(ge=1, le=500)] = 20,
    source: Annotated[str | None, Query(pattern="^(AB|AP)$")] = None,
) -> list[dict]:
    """Clients les plus à risque (identifiants pseudonymes uniquement)."""
    df = portfolio()
    if source:
        df = df[df["source_systeme"] == source]
    top = df.head(n).reset_index()
    return top[["id_client_uni", "source_systeme", "score", "niveau_risque"]].to_dict("records")


@app.get("/v1/resiliation/clients/{id_client_uni}", tags=["résiliation"])
def score_client(id_client_uni: str, user: Annotated[dict, Depends(current_user)]) -> dict:
    df = portfolio()
    if id_client_uni not in df.index:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client inconnu")
    result = predictor().predict_one(df.loc[id_client_uni])
    log_prediction(
        artifacts_dir() / "journal_predictions.jsonl", id_client_uni, result, user["sub"]
    )
    return {"id_client_uni": id_client_uni, **result}


@app.post("/v1/resiliation/score", tags=["résiliation"])
def score_features(features: ClientFeatures, user: Annotated[dict, Depends(current_user)]) -> dict:
    row = pd.Series(features.model_dump(), dtype="float64")
    return predictor().predict_one(row)


@app.get("/admin/qualite", tags=["administration"])
def quality_report(user: Annotated[dict, Depends(admin_user)]) -> dict:
    return json.loads((artifacts_dir() / "pipeline_report.json").read_text(encoding="utf-8"))


@app.get("/admin/journal", tags=["administration"])
def prediction_journal(
    user: Annotated[dict, Depends(admin_user)], n: Annotated[int, Query(ge=1, le=1000)] = 50
) -> list[dict]:
    journal = artifacts_dir() / "journal_predictions.jsonl"
    if not journal.exists():
        return []
    lines = journal.read_text(encoding="utf-8").strip().splitlines()[-n:]
    return [json.loads(line) for line in lines]
