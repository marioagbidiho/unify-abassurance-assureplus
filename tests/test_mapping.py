"""Tests des règles de mapping vers le modèle canonique et de la spécification de mapping."""

from datetime import date, datetime

import pandas as pd
import pytest
import yaml

from unify.common.security import decrypt
from unify.config import PROJECT_ROOT
from unify.mapping import canonical as c
from unify.mapping.referentials import referential_frame, translate


# ------------------------------------------------------------------ référentiels
@pytest.mark.parametrize(
    "ref, source, raw, expected",
    [
        ("ref_produit", "AB", "Santé", "SANTE"),
        ("ref_produit", "AB", "mrh", "HABITATION"),
        ("ref_produit", "AP", "AUTO_PREMIUM", "AUTO"),
        ("ref_statut_contrat", "AB", "Résilié", "RESILIE"),
        ("ref_statut_contrat", "AP", "cancelled", "RESILIE"),
        ("ref_statut_sinistre", "AP", "REJECTED", "REFUSE"),
        ("ref_produit", "AP", "INCONNU", None),
        ("ref_produit", "AB", None, None),
    ],
)
def test_translate(ref, source, raw, expected):
    assert translate(ref, source, raw) == expected


def test_translate_unknown_referential():
    with pytest.raises(KeyError):
        translate("ref_absent", "AB", "X")


def test_referential_frame_has_canonical_columns():
    df = referential_frame("ref_produit")
    assert list(df.columns) == [
        "code_uni",
        "libelle",
        "code_source_ab",
        "code_source_ap",
        "domaine",
    ]
    assert set(df["code_uni"]) == {"AUTO", "HABITATION", "SANTE", "PRO", "ASSISTANCE"}


# ------------------------------------------------------------------ helpers
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("12,345", 12.35),
        ("10.005", 10.01),
        (None, None),
        ("abc", None),
        (float("nan"), None),
        (100, 100.0),
    ],
)
def test_to_amount_rounds_half_up(raw, expected):
    assert c.to_amount(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        (42.0, "42"),
        ("ABC-1", "abc-1"),
        ("6F9619FF-8B86-D011-B42D-00C04FC964FF", "6f9619ff-8b86-d011-b42d-00c04fc964ff"),
        (None, None),
    ],
)
def test_source_id_text(raw, expected):
    assert c.source_id_text(raw) == expected


def test_row_hash_is_order_independent():
    assert c.row_hash({"a": 1, "b": 2}) == c.row_hash({"b": 2, "a": 1})
    assert c.row_hash({"a": 1}) != c.row_hash({"a": 2})


# ------------------------------------------------------------------ clients
def test_map_ab_client(settings):
    raw = pd.DataFrame(
        [
            {
                "AB_CLIENT_ID": 10,
                "AB_NOM": " dupont ",
                "AB_PRENOM": "jean",
                "AB_DATE_NAISSANCE": "1980-02-03",
                "AB_EMAIL": "JEAN.DUPONT@GMAIL.COM",
                "AB_TELEPHONE": "06 12 34 56 78",
                "AB_ADRESSE": "1  rue X",
                "AB_CODE_POSTAL": "6000",
                "AB_NUM_FISCAL": "1234567890123",
                "AB_DATE_CREATION": "2020-01-15 10:00:00",
                "AB_STATUT_CLIENT": "A",
            }
        ]
    )
    out = c.map_ab_client(raw, settings).iloc[0]
    assert out["nom"] == "DUPONT" and out["prenom"] == "Jean"
    assert out["date_naissance"] == date(1980, 2, 3)
    assert out["code_postal"] == "06000"
    assert out["statut_client"] == "ACTIF"
    assert out["date_creation_utc"] == datetime(2020, 1, 15, 9, 0)
    assert decrypt(out["num_fiscal_chiffre"], settings.crypto_key_hex) == "1234567890123"
    assert decrypt(out["telephone_chiffre"], settings.crypto_key_hex) == "+33612345678"
    assert len(out["email_hash"]) == 64 and "@" not in out["email_hash"]
    assert out["score_fidelite"] is None and out["anomalies"] is None
    assert list(c.map_ab_client(raw, settings).columns) == c.CLIENT_COLUMNS


def test_map_ap_users_flags_anomalies(settings):
    raw = pd.DataFrame(
        [
            {
                "AP_USER_ID": 7,
                "AP_FULL_NAME": "Marie MARTIN",
                "AP_BIRTH_DATE": "1985-13-45",
                "AP_MAIL_ADDRESS": "pas un mail",
                "AP_PHONE_NUMBER": "123",
                "AP_STREET_ADDRESS": "2 AV Y",
                "AP_ZIP_CODE": "69003",
                "AP_CREATED_AT": "2021-07-01 14:00:00",
                "AP_CUSTOMER_STATUS": "VIP",
                "AP_LOYALTY_SCORE": 999,
            }
        ]
    )
    out = c.map_ap_users(raw, settings).iloc[0]
    assert (out["nom"], out["prenom"]) == ("MARTIN", "Marie")
    assert out["date_naissance"] is None and out["email_hash"] is None
    assert set(out["anomalies"].split("|")) == {
        "DATE_NAISSANCE_INVALIDE",
        "EMAIL_INVALIDE",
        "TELEPHONE_INVALIDE",
        "REF_INCONNU",
        "SCORE_FIDELITE_HORS_BORNES",
    }
    assert out["score_fidelite"] is None
    assert out["date_creation_utc"] == datetime(2021, 7, 1, 12, 0)  # heure d'été : UTC+2


def test_same_email_gives_same_pseudonym_across_sources(settings):
    ab = c.map_ab_client(pd.DataFrame([{"AB_CLIENT_ID": 1, "AB_EMAIL": "X@Y.FR"}]), settings)
    ap = c.map_ap_users(pd.DataFrame([{"AP_USER_ID": 1, "AP_MAIL_ADDRESS": " x@y.fr"}]), settings)
    assert ab.loc[0, "email_hash"] == ap.loc[0, "email_hash"]
    assert ab.loc[0, "id_client_uni"] != ap.loc[0, "id_client_uni"]


# ------------------------------------------------------------------ contrats
def test_map_ap_contracts_converts_monthly_premium():
    raw = pd.DataFrame(
        [
            {
                "AP_CONTRACT_REF": "apc1",
                "AP_USER_ID": 7,
                "AP_PRODUCT_CODE": "AUTO_STD",
                "AP_START_DATE": "2024-01-01 00:00:00",
                "AP_END_DATE": "2025-01-01 00:00:00",
                "AP_MONTHLY_PREMIUM": "45.50",
                "AP_CONTRACT_STATE": "ACTIVE",
                "AP_BROKER_CODE": "BRK-1",
            }
        ]
    )
    out = c.map_ap_contracts(raw).iloc[0]
    assert out["prime_annuelle_eur"] == 546.0
    assert out["periodicite_source"] == "MENSUELLE" and out["canal_distribution"] == "COURTIER"
    assert out["ref_contrat_source"] == "APC1" and out["code_produit_uni"] == "AUTO"
    assert out["id_client_uni"] == c.canonical_id("client", "AP", "7")


def test_map_ab_contrat_detects_incoherent_period():
    raw = pd.DataFrame(
        [
            {
                "AB_POLICY_NUMBER": "AB-1",
                "AB_CLIENT_ID": 1,
                "AB_TYPE_ASSURANCE": "AUTO",
                "AB_DATE_DEBUT": "2024-05-01",
                "AB_DATE_FIN": "2024-01-01",
                "AB_PRIME_ANNUELLE": 600,
                "AB_STATUT_CONTRAT": "EN_COURS",
                "AB_AGENCE_ID": 101,
            }
        ]
    )
    out = c.map_ab_contrat(raw).iloc[0]
    assert out["anomalies"] == "PERIODE_INCOHERENTE"
    assert out["prime_annuelle_eur"] == 600.0 and out["periodicite_source"] == "ANNUELLE"
    assert out["canal_distribution"] == "AGENCE" and out["id_distributeur"] == "101"


# ------------------------------------------------------------------ sinistres / paiements
def test_map_ap_claims_text_date_and_redaction(settings):
    raw = pd.DataFrame(
        [
            {
                "AP_SINISTRE_NUM": 1,
                "AP_CONTRACT_REF": "APC1",
                "AP_INCIDENT_DATE": "2026-01-10 08:00:00",
                "AP_ESTIMATED_AMOUNT": -5,
                "AP_CLAIM_STATUS": "PAID",
                "AP_CLAIM_COMMENT": "Rappeler au 06 12 34 56 78",
                "AP_FRAUD_SCORE": 12.5,
            }
        ]
    )
    out = c.map_ap_claims(raw, settings).iloc[0]
    assert out["date_heure_sinistre_utc"] == datetime(2026, 1, 10, 7, 0)
    assert out["statut_sinistre"] == "CLOS_INDEMNISE"
    assert out["description_texte"] == "Rappeler au [TEL]"
    assert out["anomalies"] == "MONTANT_NEGATIF" and out["score_fraude_source"] == 12.5
    assert out["id_contrat_uni"] == c.canonical_id("contrat", "AP", "apc1")


def test_map_ab_sinistre(settings):
    raw = pd.DataFrame(
        [
            {
                "AB_CLAIM_ID": 5,
                "AB_POLICY_NUMBER": "AB-1",
                "AB_DATE_SINISTRE": "2026-36-01 10:00:00",
                "AB_MONTANT_ESTIME": 100,
                "AB_STATUT_SINISTRE": "REFUSE",
                "AB_DESCRIPTION": None,
            }
        ]
    )
    out = c.map_ab_sinistre(raw, settings).iloc[0]
    assert out["date_heure_sinistre_utc"] is None
    assert out["anomalies"] == "DATE_SINISTRE_INVALIDE" and out["statut_sinistre"] == "REFUSE"
    assert out["score_fraude_source"] is None


def test_map_payments(settings):
    ab = c.map_ab_paiement(
        pd.DataFrame(
            [
                {
                    "AB_PAYMENT_ID": 1,
                    "AB_POLICY_NUMBER": "AB-1",
                    "AB_DATE_PAIEMENT": "2026-06-01",
                    "AB_MONTANT": 0,
                    "AB_MODE_PAIEMENT": "Prélèvement",
                }
            ]
        ),
        settings,
    ).iloc[0]
    assert ab["statut_transaction"] == "NON_RENSEIGNE" and ab["moyen_paiement"] == "PRELEVEMENT"
    assert ab["anomalies"] == "MONTANT_NON_POSITIF"
    ap = c.map_ap_payments(
        pd.DataFrame(
            [
                {
                    "AP_PAYMENT_REF": "6F9619FF-8B86-D011-B42D-00C04FC964FF",
                    "AP_CONTRACT_REF": "APC1",
                    "AP_PAYMENT_DATETIME": "2026-06-01 10:00:00",
                    "AP_AMOUNT_PAID": 45.5,
                    "AP_PAYMENT_CHANNEL": "MOBILE_APP",
                    "AP_TRANSACTION_STATUS": "FAILED",
                }
            ]
        ),
        settings,
    ).iloc[0]
    assert ap["statut_transaction"] == "REJETE" and ap["moyen_paiement"] == "APPLI_MOBILE"
    assert ap["id_paiement_source"] == "6f9619ff-8b86-d011-b42d-00c04fc964ff"


# ------------------------------------------------------------------ spécification
def test_every_source_column_is_mapped(small_tables):
    """Chaque colonne des 8 tables sources (schémas de l'annexe) figure dans mapping.yaml."""
    spec = yaml.safe_load((PROJECT_ROOT / "config" / "mapping.yaml").read_text(encoding="utf-8"))
    assert set(spec) == set(small_tables) == set(c.MAPPERS)
    for table, df in small_tables.items():
        mapped = {col[0] for col in spec[table]["columns"]}
        assert mapped == set(df.columns), table


def test_all_mappers_produce_canonical_columns(small_tables, settings):
    for table, (entity, mapper) in c.MAPPERS.items():
        out = mapper(small_tables[table].head(20), settings)
        assert list(out.columns) == c.CANONICAL_COLUMNS[entity], table
        assert len(out) == min(20, len(small_tables[table]))
