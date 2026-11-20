"""Règles qualité déclaratives appliquées aux données canoniques avant écriture en SILVER.

Deux niveaux de sévérité :
* ``CRITIQUE``      -> la ligne est isolée en quarantaine (D6, /data/quarantaine) avec le motif ;
* ``AVERTISSEMENT`` -> la ligne est conservée, l'anomalie est comptée dans le rapport qualité.

Les règles sont l'équivalent « léger » d'une suite Great Expectations : une règle = un nom,
une sévérité, un prédicat vectorisé. Le rapport est publié pour le tableau de bord P9.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

CRITIQUE = "CRITIQUE"
AVERTISSEMENT = "AVERTISSEMENT"

KEYS = {
    "client": "id_client_uni",
    "contrat": "id_contrat_uni",
    "sinistre": "id_sinistre_uni",
    "paiement": "id_paiement_uni",
}
PARENT_KEYS = {
    "contrat": "id_client_uni",
    "sinistre": "id_contrat_uni",
    "paiement": "id_contrat_uni",
}


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str
    description: str
    failed: Callable[[pd.DataFrame], pd.Series]


def _has_anomaly(code: str) -> Callable[[pd.DataFrame], pd.Series]:
    def check(df: pd.DataFrame) -> pd.Series:
        return df["anomalies"].fillna("").str.split("|").apply(lambda codes: code in codes)

    return check


def _is_null(column: str) -> Callable[[pd.DataFrame], pd.Series]:
    return lambda df: df[column].isna()


def _age_implausible(df: pd.DataFrame) -> pd.Series:
    births = pd.to_datetime(df["date_naissance"], errors="coerce")
    today = pd.Timestamp(date.today())
    too_old = births < pd.Timestamp("1900-01-01")
    too_young = births > today - pd.DateOffset(years=16)
    return (too_old | too_young).fillna(False)


def _prime_invalid(df: pd.DataFrame) -> pd.Series:
    return df["prime_annuelle_eur"].isna() | (df["prime_annuelle_eur"] <= 0)


def rules_for(entity: str) -> list[Rule]:
    key = KEYS[entity]
    common = [
        Rule("CLE_ABSENTE", CRITIQUE, f"{key} non calculable (id source vide)", _is_null(key)),
        Rule(
            "REF_INCONNU",
            CRITIQUE if entity in {"client", "contrat"} else AVERTISSEMENT,
            "Code absent des référentiels ref_*",
            _has_anomaly("REF_INCONNU"),
        ),
    ]
    specific: dict[str, list[Rule]] = {
        "client": [
            Rule("NOM_ABSENT", CRITIQUE, "Nom du client vide", _is_null("nom")),
            Rule(
                "DATE_NAISSANCE_INVALIDE",
                CRITIQUE,
                "Date de naissance non convertible",
                _has_anomaly("DATE_NAISSANCE_INVALIDE"),
            ),
            Rule(
                "AGE_INVRAISEMBLABLE",
                CRITIQUE,
                "Né avant 1900 ou âgé de moins de 16 ans",
                _age_implausible,
            ),
            Rule(
                "EMAIL_INVALIDE",
                AVERTISSEMENT,
                "E-mail au format invalide (mis à NULL)",
                _has_anomaly("EMAIL_INVALIDE"),
            ),
            Rule(
                "TELEPHONE_INVALIDE",
                AVERTISSEMENT,
                "Téléphone non normalisable en E.164",
                _has_anomaly("TELEPHONE_INVALIDE"),
            ),
        ],
        "contrat": [
            Rule("CLIENT_ABSENT", CRITIQUE, "Contrat sans client", _is_null("id_client_uni")),
            Rule(
                "DATE_DEBUT_INVALIDE",
                CRITIQUE,
                "Date de début absente ou invalide",
                _is_null("date_debut"),
            ),
            Rule(
                "PERIODE_INCOHERENTE",
                CRITIQUE,
                "date_fin antérieure à date_debut",
                _has_anomaly("PERIODE_INCOHERENTE"),
            ),
            Rule("PRIME_INVALIDE", CRITIQUE, "Prime absente ou <= 0", _prime_invalid),
        ],
        "sinistre": [
            Rule(
                "DATE_SINISTRE_INVALIDE",
                CRITIQUE,
                "Date de sinistre non convertible",
                _is_null("date_heure_sinistre_utc"),
            ),
            Rule(
                "MONTANT_NEGATIF",
                CRITIQUE,
                "Montant estimé négatif",
                _has_anomaly("MONTANT_NEGATIF"),
            ),
        ],
        "paiement": [
            Rule(
                "DATE_PAIEMENT_INVALIDE",
                CRITIQUE,
                "Date de paiement non convertible",
                _is_null("date_heure_paiement_utc"),
            ),
            Rule(
                "MONTANT_NON_POSITIF",
                CRITIQUE,
                "Montant payé <= 0 ou absent",
                lambda df: df["montant_eur"].isna() | (df["montant_eur"] <= 0),
            ),
        ],
    }
    return common + specific[entity]


@dataclass
class QualityResult:
    entity: str
    valid: pd.DataFrame
    rejects: pd.DataFrame
    report: dict = field(default_factory=dict)


def deduplicate(df: pd.DataFrame, entity: str) -> tuple[pd.DataFrame, int]:
    """Supprime les doublons de clé (rejeu Kafka, lignes dupliquées en source).

    À clé égale on garde la version la plus récemment ingérée.
    """
    key = KEYS[entity]
    before = len(df)
    ordered = df.sort_values("dt_ingestion_utc", kind="stable")
    deduped = ordered.drop_duplicates(subset=[key], keep="last")
    return deduped.sort_index(), before - len(deduped)


def evaluate_rules(
    df: pd.DataFrame, entity: str, parent_ids: set[str] | None = None
) -> tuple[pd.Series, dict[str, dict]]:
    """Évalue les règles ligne à ligne.

    Retourne le motif de rejet de chaque ligne ("" si la ligne est conforme) et le nombre
    de lignes en échec par règle. Fonction pure, réutilisée par Spark (``mapInPandas``).
    """
    rules = rules_for(entity)
    if parent_ids is not None and entity in PARENT_KEYS:
        parent = PARENT_KEYS[entity]
        rules.append(
            Rule(
                "INTEGRITE_REFERENTIELLE",
                CRITIQUE,
                f"{parent} inexistant (ou en quarantaine)",
                lambda d, p=parent: d[p].notna() & ~d[p].isin(parent_ids),
            )
        )
    reasons = pd.Series("", index=df.index, dtype="object")
    counts: dict[str, dict] = {}
    for rule in rules:
        failed = rule.failed(df).fillna(False).astype(bool)
        counts[rule.name] = {
            "severite": rule.severity,
            "description": rule.description,
            "nb_lignes": int(failed.sum()),
        }
        if rule.severity == CRITIQUE:
            reasons = reasons.where(~failed, reasons + "|" + rule.name)
    return reasons.str.strip("|"), counts


def apply_quality(
    df: pd.DataFrame, entity: str, parent_ids: set[str] | None = None
) -> QualityResult:
    """Dédoublonne, applique les règles, sépare valides / rejets et produit le rapport."""
    df = df.reset_index(drop=True)
    deduped, nb_doublons = deduplicate(df, entity)
    deduped = deduped.reset_index(drop=True)
    reasons, counts = evaluate_rules(deduped, entity, parent_ids)
    rejected = reasons != ""
    valid = deduped.loc[~rejected].copy()
    rejects = deduped.loc[rejected].copy()
    rejects["motif_rejet"] = reasons[rejected]
    return QualityResult(
        entity,
        valid,
        rejects,
        build_report(entity, len(df), nb_doublons, len(valid), len(rejects), counts),
    )


def build_report(
    entity: str,
    nb_lues: int,
    nb_doublons: int,
    nb_valides: int,
    nb_rejets: int,
    counts: dict[str, dict],
) -> dict:
    total = nb_valides + nb_rejets
    return {
        "entite": entity,
        "nb_lignes_lues": nb_lues,
        "nb_doublons_supprimes": nb_doublons,
        "nb_lignes_valides": nb_valides,
        "nb_lignes_quarantaine": nb_rejets,
        "taux_conformite": round(nb_valides / total, 4) if total else 1.0,
        "regles": counts,
    }
