"""Règles de transformation BRONZE -> SILVER (modèle canonique uni_*).

Chaque fonction ``map_<source>_<table>`` reçoit un DataFrame pandas aux colonnes d'origine
(AB_* ou AP_*) et retourne un DataFrame aux colonnes canoniques. Les fonctions sont pures
(aucune E/S) : elles sont exécutées telles quelles par Spark via ``mapInPandas`` (une seule
implémentation des règles, testée unitairement sans cluster).

Les valeurs non convertibles ne sont jamais « devinées » : la colonne ``anomalies`` liste
les problèmes détectés (codes séparés par « | ») ; le module ``unify.quality`` décide
ensuite si la ligne part en quarantaine.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import pandas as pd

from unify.common import dates, security, text
from unify.common.ids import canonical_id
from unify.config import Settings, get_settings
from unify.mapping.referentials import translate

TECH_COLUMNS = ["source_systeme", "dt_ingestion_utc", "hash_ligne", "anomalies"]

CLIENT_COLUMNS = [
    "id_client_uni",
    "id_personne_mdm",
    "id_client_source",
    "nom",
    "prenom",
    "date_naissance",
    "email_hash",
    "telephone_chiffre",
    "adresse",
    "code_postal",
    "pays",
    "num_fiscal_chiffre",
    "date_creation_utc",
    "statut_client",
    "score_fidelite",
    *TECH_COLUMNS,
]
CONTRAT_COLUMNS = [
    "id_contrat_uni",
    "id_client_uni",
    "ref_contrat_source",
    "code_produit_uni",
    "date_debut",
    "date_fin",
    "prime_annuelle_eur",
    "periodicite_source",
    "statut_contrat",
    "canal_distribution",
    "id_distributeur",
    *TECH_COLUMNS,
]
SINISTRE_COLUMNS = [
    "id_sinistre_uni",
    "id_contrat_uni",
    "id_sinistre_source",
    "date_heure_sinistre_utc",
    "montant_estime_eur",
    "statut_sinistre",
    "description_texte",
    "score_fraude_source",
    *TECH_COLUMNS,
]
PAIEMENT_COLUMNS = [
    "id_paiement_uni",
    "id_contrat_uni",
    "id_paiement_source",
    "date_heure_paiement_utc",
    "montant_eur",
    "moyen_paiement",
    "statut_transaction",
    *TECH_COLUMNS,
]

CANONICAL_COLUMNS = {
    "client": CLIENT_COLUMNS,
    "contrat": CONTRAT_COLUMNS,
    "sinistre": SINISTRE_COLUMNS,
    "paiement": PAIEMENT_COLUMNS,
}

_TWO_PLACES = Decimal("0.01")


# ----------------------------------------------------------------------------- helpers
def row_hash(row: dict) -> str:
    """Empreinte SHA-256 du contenu source (détection des changements, idempotence)."""
    payload = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def to_amount(value: object) -> float | None:
    """Montant arrondi au centime (arrondi commercial) ; None si non numérique."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        amount = Decimal(str(value).replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return None
    if amount.is_nan():
        return None
    return float(amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def source_id_text(value: object) -> str | None:
    """Identifiant source en texte (NUMBER 42.0 -> '42', GUID -> minuscules)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    cleaned = text.clean_str(value)
    return cleaned.lower() if cleaned else None


def _translated(
    ref: str, source: str, raw: object, anomaly: str, anomalies: list[str]
) -> str | None:
    code = translate(ref, source, raw)
    if code is None and text.clean_str(raw) is not None:
        anomalies.append(anomaly)
    return code


def _checked_date(raw: object, anomaly: str, anomalies: list[str]):
    value = dates.parse_date(raw)
    if value is None and text.clean_str(None if raw is None else str(raw)) is not None:
        anomalies.append(anomaly)
    return value


def _checked_datetime_utc(raw: object, tz: str, anomaly: str, anomalies: list[str]):
    value = dates.parse_datetime(raw)
    if value is None and text.clean_str(None if raw is None else str(raw)) is not None:
        anomalies.append(anomaly)
    return dates.to_utc(value, tz)


def _frame(records: Iterable[dict], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(list(records), columns=columns)


def _tech(source: str, raw: dict, anomalies: list[str]) -> dict:
    return {
        "source_systeme": source,
        "dt_ingestion_utc": datetime.now(UTC).replace(tzinfo=None),
        "hash_ligne": row_hash(raw),
        "anomalies": "|".join(anomalies) or None,
    }


def _contrat_key(source: str, ref: object) -> str | None:
    ref_txt = source_id_text(ref)
    return canonical_id("contrat", source, ref_txt) if ref_txt else None


# ----------------------------------------------------------------------------- CLIENT
def _client_common(raw: dict, s: Settings, anomalies: list[str], **values) -> dict:
    email_raw = values.pop("email_raw")
    email = text.normalize_email(email_raw)
    if email is None and text.clean_str(email_raw) is not None:
        anomalies.append("EMAIL_INVALIDE")
    phone_raw = values.pop("phone_raw")
    phone = text.normalize_phone(phone_raw)
    if phone is None and text.clean_str(phone_raw) is not None:
        anomalies.append("TELEPHONE_INVALIDE")
    return {
        "id_personne_mdm": None,
        "email_hash": security.pseudonymize(email, s.pseudo_key),
        "telephone_chiffre": security.encrypt(phone, s.crypto_key_hex),
        "pays": "FR",
        **values,
    }


def map_ab_client(df: pd.DataFrame, settings: Settings | None = None) -> pd.DataFrame:
    s = settings or get_settings()
    out = []
    for raw in df.to_dict("records"):
        anomalies: list[str] = []
        id_src = source_id_text(raw.get("AB_CLIENT_ID"))
        prenom = text.clean_str(raw.get("AB_PRENOM"))
        nom = text.clean_str(raw.get("AB_NOM"))
        num_fiscal = text.clean_str(raw.get("AB_NUM_FISCAL"))
        out.append(
            {
                "id_client_uni": canonical_id("client", "AB", id_src) if id_src else None,
                "id_client_source": id_src,
                **_client_common(
                    raw,
                    s,
                    anomalies,
                    email_raw=raw.get("AB_EMAIL"),
                    phone_raw=raw.get("AB_TELEPHONE"),
                    nom=nom.upper() if nom else None,
                    prenom=prenom.title() if prenom else None,
                    date_naissance=_checked_date(
                        raw.get("AB_DATE_NAISSANCE"), "DATE_NAISSANCE_INVALIDE", anomalies
                    ),
                    adresse=text.clean_str(raw.get("AB_ADRESSE")),
                    code_postal=text.normalize_postal_code(raw.get("AB_CODE_POSTAL")),
                    num_fiscal_chiffre=security.encrypt(num_fiscal, s.crypto_key_hex),
                    date_creation_utc=_checked_datetime_utc(
                        raw.get("AB_DATE_CREATION"),
                        s.source_timezone,
                        "DATE_CREATION_INVALIDE",
                        anomalies,
                    ),
                    statut_client=_translated(
                        "ref_statut_client",
                        "AB",
                        raw.get("AB_STATUT_CLIENT"),
                        "REF_INCONNU",
                        anomalies,
                    ),
                    score_fidelite=None,
                ),
                **_tech("AB", raw, anomalies),
            }
        )
    return _frame(out, CLIENT_COLUMNS)


def map_ap_users(df: pd.DataFrame, settings: Settings | None = None) -> pd.DataFrame:
    s = settings or get_settings()
    out = []
    for raw in df.to_dict("records"):
        anomalies: list[str] = []
        id_src = source_id_text(raw.get("AP_USER_ID"))
        nom, prenom = text.split_full_name(raw.get("AP_FULL_NAME"))
        loyalty = raw.get("AP_LOYALTY_SCORE")
        loyalty = None if loyalty is None or pd.isna(loyalty) else int(loyalty)
        if loyalty is not None and not 0 <= loyalty <= 100:
            anomalies.append("SCORE_FIDELITE_HORS_BORNES")
            loyalty = None
        out.append(
            {
                "id_client_uni": canonical_id("client", "AP", id_src) if id_src else None,
                "id_client_source": id_src,
                **_client_common(
                    raw,
                    s,
                    anomalies,
                    email_raw=raw.get("AP_MAIL_ADDRESS"),
                    phone_raw=raw.get("AP_PHONE_NUMBER"),
                    nom=nom,
                    prenom=prenom,
                    date_naissance=_checked_date(
                        raw.get("AP_BIRTH_DATE"), "DATE_NAISSANCE_INVALIDE", anomalies
                    ),
                    adresse=text.clean_str(raw.get("AP_STREET_ADDRESS")),
                    code_postal=text.normalize_postal_code(raw.get("AP_ZIP_CODE")),
                    num_fiscal_chiffre=None,
                    date_creation_utc=_checked_datetime_utc(
                        raw.get("AP_CREATED_AT"),
                        s.source_timezone,
                        "DATE_CREATION_INVALIDE",
                        anomalies,
                    ),
                    statut_client=_translated(
                        "ref_statut_client",
                        "AP",
                        raw.get("AP_CUSTOMER_STATUS"),
                        "REF_INCONNU",
                        anomalies,
                    ),
                    score_fidelite=loyalty,
                ),
                **_tech("AP", raw, anomalies),
            }
        )
    return _frame(out, CLIENT_COLUMNS)


# ----------------------------------------------------------------------------- CONTRAT
def _contrat(raw: dict, source: str, cols: dict[str, str], monthly: bool) -> dict:
    anomalies: list[str] = []
    ref = source_id_text(raw.get(cols["ref"]))
    client_src = source_id_text(raw.get(cols["client"]))
    prime = to_amount(raw.get(cols["prime"]))
    if prime is not None and monthly:
        prime = round(prime * 12, 2)
    date_debut = _checked_date(raw.get(cols["debut"]), "DATE_DEBUT_INVALIDE", anomalies)
    date_fin = _checked_date(raw.get(cols["fin"]), "DATE_FIN_INVALIDE", anomalies)
    if date_debut and date_fin and date_fin < date_debut:
        anomalies.append("PERIODE_INCOHERENTE")
    return {
        "id_contrat_uni": canonical_id("contrat", source, ref) if ref else None,
        "id_client_uni": canonical_id("client", source, client_src) if client_src else None,
        "ref_contrat_source": ref.upper() if ref else None,
        "code_produit_uni": _translated(
            "ref_produit", source, raw.get(cols["produit"]), "REF_INCONNU", anomalies
        ),
        "date_debut": date_debut,
        "date_fin": date_fin,
        "prime_annuelle_eur": prime,
        "periodicite_source": "MENSUELLE" if monthly else "ANNUELLE",
        "statut_contrat": _translated(
            "ref_statut_contrat", source, raw.get(cols["statut"]), "REF_INCONNU", anomalies
        ),
        "canal_distribution": "COURTIER" if source == "AP" else "AGENCE",
        "id_distributeur": source_id_text(raw.get(cols["distrib"])),
        **_tech(source, raw, anomalies),
    }


_AB_CONTRAT = {
    "ref": "AB_POLICY_NUMBER",
    "client": "AB_CLIENT_ID",
    "prime": "AB_PRIME_ANNUELLE",
    "debut": "AB_DATE_DEBUT",
    "fin": "AB_DATE_FIN",
    "produit": "AB_TYPE_ASSURANCE",
    "statut": "AB_STATUT_CONTRAT",
    "distrib": "AB_AGENCE_ID",
}
_AP_CONTRAT = {
    "ref": "AP_CONTRACT_REF",
    "client": "AP_USER_ID",
    "prime": "AP_MONTHLY_PREMIUM",
    "debut": "AP_START_DATE",
    "fin": "AP_END_DATE",
    "produit": "AP_PRODUCT_CODE",
    "statut": "AP_CONTRACT_STATE",
    "distrib": "AP_BROKER_CODE",
}


def map_ab_contrat(df: pd.DataFrame, settings: Settings | None = None) -> pd.DataFrame:
    return _frame(
        (_contrat(r, "AB", _AB_CONTRAT, False) for r in df.to_dict("records")), CONTRAT_COLUMNS
    )


def map_ap_contracts(df: pd.DataFrame, settings: Settings | None = None) -> pd.DataFrame:
    return _frame(
        (_contrat(r, "AP", _AP_CONTRAT, True) for r in df.to_dict("records")), CONTRAT_COLUMNS
    )


# ----------------------------------------------------------------------------- SINISTRE
def _sinistre(raw: dict, source: str, cols: dict[str, str], s: Settings) -> dict:
    anomalies: list[str] = []
    id_src = source_id_text(raw.get(cols["id"]))
    montant = to_amount(raw.get(cols["montant"]))
    if montant is not None and montant < 0:
        anomalies.append("MONTANT_NEGATIF")
    fraud = to_amount(raw.get("AP_FRAUD_SCORE")) if source == "AP" else None
    return {
        "id_sinistre_uni": canonical_id("sinistre", source, id_src) if id_src else None,
        "id_contrat_uni": _contrat_key(source, raw.get(cols["contrat"])),
        "id_sinistre_source": id_src,
        "date_heure_sinistre_utc": _checked_datetime_utc(
            raw.get(cols["date"]), s.source_timezone, "DATE_SINISTRE_INVALIDE", anomalies
        ),
        "montant_estime_eur": montant,
        "statut_sinistre": _translated(
            "ref_statut_sinistre", source, raw.get(cols["statut"]), "REF_INCONNU", anomalies
        ),
        "description_texte": security.redact_free_text(text.clean_str(raw.get(cols["texte"]))),
        "score_fraude_source": fraud,
        **_tech(source, raw, anomalies),
    }


_AB_SIN = {
    "id": "AB_CLAIM_ID",
    "contrat": "AB_POLICY_NUMBER",
    "date": "AB_DATE_SINISTRE",
    "montant": "AB_MONTANT_ESTIME",
    "statut": "AB_STATUT_SINISTRE",
    "texte": "AB_DESCRIPTION",
}
_AP_SIN = {
    "id": "AP_SINISTRE_NUM",
    "contrat": "AP_CONTRACT_REF",
    "date": "AP_INCIDENT_DATE",
    "montant": "AP_ESTIMATED_AMOUNT",
    "statut": "AP_CLAIM_STATUS",
    "texte": "AP_CLAIM_COMMENT",
}


def map_ab_sinistre(df: pd.DataFrame, settings: Settings | None = None) -> pd.DataFrame:
    s = settings or get_settings()
    return _frame((_sinistre(r, "AB", _AB_SIN, s) for r in df.to_dict("records")), SINISTRE_COLUMNS)


def map_ap_claims(df: pd.DataFrame, settings: Settings | None = None) -> pd.DataFrame:
    s = settings or get_settings()
    return _frame((_sinistre(r, "AP", _AP_SIN, s) for r in df.to_dict("records")), SINISTRE_COLUMNS)


# ----------------------------------------------------------------------------- PAIEMENT
def _paiement(raw: dict, source: str, cols: dict[str, str], s: Settings) -> dict:
    anomalies: list[str] = []
    id_src = source_id_text(raw.get(cols["id"]))
    montant = to_amount(raw.get(cols["montant"]))
    if montant is not None and montant <= 0:
        anomalies.append("MONTANT_NON_POSITIF")
    if source == "AP":
        statut = _translated(
            "ref_statut_transaction",
            "AP",
            raw.get("AP_TRANSACTION_STATUS"),
            "REF_INCONNU",
            anomalies,
        )
    else:
        statut = "NON_RENSEIGNE"
    return {
        # Clé déterministe (UUID v5) : un paiement rejoué garde la même clé -> idempotence
        "id_paiement_uni": canonical_id("paiement", source, id_src) if id_src else None,
        "id_contrat_uni": _contrat_key(source, raw.get(cols["contrat"])),
        "id_paiement_source": id_src,
        "date_heure_paiement_utc": _checked_datetime_utc(
            raw.get(cols["date"]), s.source_timezone, "DATE_PAIEMENT_INVALIDE", anomalies
        ),
        "montant_eur": montant,
        "moyen_paiement": _translated(
            "ref_moyen_paiement", source, raw.get(cols["moyen"]), "REF_INCONNU", anomalies
        ),
        "statut_transaction": statut,
        **_tech(source, raw, anomalies),
    }


_AB_PAY = {
    "id": "AB_PAYMENT_ID",
    "contrat": "AB_POLICY_NUMBER",
    "date": "AB_DATE_PAIEMENT",
    "montant": "AB_MONTANT",
    "moyen": "AB_MODE_PAIEMENT",
}
_AP_PAY = {
    "id": "AP_PAYMENT_REF",
    "contrat": "AP_CONTRACT_REF",
    "date": "AP_PAYMENT_DATETIME",
    "montant": "AP_AMOUNT_PAID",
    "moyen": "AP_PAYMENT_CHANNEL",
}


def map_ab_paiement(df: pd.DataFrame, settings: Settings | None = None) -> pd.DataFrame:
    s = settings or get_settings()
    return _frame((_paiement(r, "AB", _AB_PAY, s) for r in df.to_dict("records")), PAIEMENT_COLUMNS)


def map_ap_payments(df: pd.DataFrame, settings: Settings | None = None) -> pd.DataFrame:
    s = settings or get_settings()
    return _frame((_paiement(r, "AP", _AP_PAY, s) for r in df.to_dict("records")), PAIEMENT_COLUMNS)


# ----------------------------------------------------------------------------- registre
MAPPERS: dict[str, tuple[str, Callable[..., pd.DataFrame]]] = {
    "AB_CLIENT": ("client", map_ab_client),
    "AP_USERS": ("client", map_ap_users),
    "AB_CONTRAT": ("contrat", map_ab_contrat),
    "AP_CONTRACTS": ("contrat", map_ap_contracts),
    "AB_SINISTRE": ("sinistre", map_ab_sinistre),
    "AP_CLAIMS": ("sinistre", map_ap_claims),
    "AB_PAIEMENT": ("paiement", map_ab_paiement),
    "AP_PAYMENTS": ("paiement", map_ap_payments),
}
