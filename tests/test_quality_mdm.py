"""Tests des règles qualité (P4) et du rapprochement client MDM (P5)."""

from datetime import date, datetime

import pandas as pd
import pytest

from unify.mdm.matching import build_golden_records, find_links, jaro_winkler
from unify.quality import apply_quality, deduplicate, evaluate_rules


def _client(id_, **kw):
    base = {
        "id_client_uni": id_,
        "id_personne_mdm": None,
        "id_client_source": id_,
        "nom": "DUPONT",
        "prenom": "Jean",
        "date_naissance": date(1980, 1, 1),
        "email_hash": None,
        "telephone_chiffre": None,
        "adresse": None,
        "code_postal": "75011",
        "pays": "FR",
        "num_fiscal_chiffre": None,
        "date_creation_utc": datetime(2020, 1, 1),
        "statut_client": "ACTIF",
        "score_fidelite": None,
        "source_systeme": "AB",
        "dt_ingestion_utc": datetime(2026, 1, 1),
        "hash_ligne": "h",
        "anomalies": None,
    }
    base.update(kw)
    return base


# ------------------------------------------------------------------ qualité
def test_quality_splits_valid_and_quarantine():
    df = pd.DataFrame(
        [
            _client("a"),
            _client("b", nom=None),
            _client("c", date_naissance=date(1850, 1, 1)),
            _client("d", anomalies="EMAIL_INVALIDE"),  # avertissement : conservé
            _client("e", anomalies="DATE_NAISSANCE_INVALIDE|REF_INCONNU", date_naissance=None),
        ]
    )
    result = apply_quality(df, "client")
    assert sorted(result.valid["id_client_uni"]) == ["a", "d"]
    motifs = dict(zip(result.rejects["id_client_uni"], result.rejects["motif_rejet"], strict=True))
    assert motifs == {
        "b": "NOM_ABSENT",
        "c": "AGE_INVRAISEMBLABLE",
        "e": "REF_INCONNU|DATE_NAISSANCE_INVALIDE",
    }
    assert result.report["taux_conformite"] == 0.4
    assert result.report["regles"]["EMAIL_INVALIDE"] == {
        "severite": "AVERTISSEMENT",
        "description": "E-mail au format invalide (mis à NULL)",
        "nb_lignes": 1,
    }


def test_deduplicate_keeps_latest_version():
    df = pd.DataFrame(
        [
            _client("a", nom="OLD", dt_ingestion_utc=datetime(2026, 1, 1)),
            _client("a", nom="NEW", dt_ingestion_utc=datetime(2026, 1, 2)),
            _client("b"),
        ]
    )
    deduped, removed = deduplicate(df, "client")
    assert removed == 1
    assert deduped.set_index("id_client_uni").loc["a", "nom"] == "NEW"


def test_referential_integrity_rule():
    contrats = pd.DataFrame(
        [
            {
                "id_contrat_uni": "k1",
                "id_client_uni": "a",
                "date_debut": date(2024, 1, 1),
                "prime_annuelle_eur": 500.0,
                "anomalies": None,
                "dt_ingestion_utc": datetime(2026, 1, 1),
            },
            {
                "id_contrat_uni": "k2",
                "id_client_uni": "zz",
                "date_debut": date(2024, 1, 1),
                "prime_annuelle_eur": -1.0,
                "anomalies": None,
                "dt_ingestion_utc": datetime(2026, 1, 1),
            },
        ]
    )
    result = apply_quality(contrats, "contrat", parent_ids={"a"})
    assert list(result.valid["id_contrat_uni"]) == ["k1"]
    assert result.rejects.iloc[0]["motif_rejet"] == "PRIME_INVALIDE|INTEGRITE_REFERENTIELLE"


def test_evaluate_rules_payments():
    df = pd.DataFrame(
        [
            {
                "id_paiement_uni": "p1",
                "id_contrat_uni": "k",
                "date_heure_paiement_utc": None,
                "montant_eur": 10.0,
                "anomalies": None,
            },
            {
                "id_paiement_uni": "p2",
                "id_contrat_uni": "k",
                "date_heure_paiement_utc": datetime(2026, 1, 1),
                "montant_eur": None,
                "anomalies": "REF_INCONNU",
            },
        ]
    )
    reasons, counts = evaluate_rules(df, "paiement")
    assert list(reasons) == ["DATE_PAIEMENT_INVALIDE", "MONTANT_NON_POSITIF"]
    assert counts["REF_INCONNU"]["severite"] == "AVERTISSEMENT"


def test_apply_quality_on_empty_frame():
    empty = pd.DataFrame(
        columns=[
            "id_sinistre_uni",
            "id_contrat_uni",
            "date_heure_sinistre_utc",
            "anomalies",
            "dt_ingestion_utc",
        ]
    )
    result = apply_quality(empty, "sinistre")
    assert result.report["taux_conformite"] == 1.0 and result.valid.empty


# ------------------------------------------------------------------ MDM
@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("MARTHA", "MARHTA", 0.961),
        ("DWAYNE", "DUANE", 0.84),
        ("ABC", "ABC", 1.0),
        ("", "ABC", 0.0),
        ("ABC", "XYZ", 0.0),
    ],
)
def test_jaro_winkler_reference_values(a, b, expected):
    assert jaro_winkler(a, b) == pytest.approx(expected, abs=1e-3)


def _mdm_frame():
    rows = [
        # même e-mail + même date -> R1
        {
            "id_client_uni": "ab1",
            "source_systeme": "AB",
            "nom": "DUPONT",
            "prenom": "Jean",
            "date_naissance": date(1980, 1, 1),
            "email_hash": "e1",
            "code_postal": "75011",
        },
        {
            "id_client_uni": "ap1",
            "source_systeme": "AP",
            "nom": "DUPONT",
            "prenom": "Jean",
            "date_naissance": date(1980, 1, 1),
            "email_hash": "e1",
            "code_postal": "75011",
        },
        # e-mail différent, identité identique -> R2
        {
            "id_client_uni": "ab2",
            "source_systeme": "AB",
            "nom": "LEFÈVRE",
            "prenom": "Hélène",
            "date_naissance": date(1975, 5, 5),
            "email_hash": "e2",
            "code_postal": "69003",
        },
        {
            "id_client_uni": "ap2",
            "source_systeme": "AP",
            "nom": "LEFEVRE",
            "prenom": "Helene",
            "date_naissance": date(1975, 5, 5),
            "email_hash": "e3",
            "code_postal": "69003",
        },
        # faute de frappe + même code postal -> R3
        {
            "id_client_uni": "ab3",
            "source_systeme": "AB",
            "nom": "AGBIDIHO",
            "prenom": "Mario",
            "date_naissance": date(1990, 9, 9),
            "email_hash": None,
            "code_postal": "13008",
        },
        {
            "id_client_uni": "ap3",
            "source_systeme": "AP",
            "nom": "AGBIDIHOU",
            "prenom": "Mario",
            "date_naissance": date(1990, 9, 9),
            "email_hash": None,
            "code_postal": "13008",
        },
        # homonyme avec une autre date de naissance -> pas de lien
        {
            "id_client_uni": "ap4",
            "source_systeme": "AP",
            "nom": "DUPONT",
            "prenom": "Jean",
            "date_naissance": date(1981, 1, 1),
            "email_hash": None,
            "code_postal": "75011",
        },
    ]
    return pd.DataFrame(rows)


def test_find_links_rules():
    links = find_links(_mdm_frame()).set_index("id_client_uni_ab")
    assert links.loc["ab1", "regle"] == "R1_EMAIL_DDN"
    assert links.loc["ab2", "regle"] == "R2_IDENTITE"
    assert links.loc["ab3", "regle"] == "R3_PROBABILISTE" and links.loc["ab3", "score"] >= 0.92
    assert "ap4" not in set(links["id_client_uni_ap"])


def test_build_golden_records():
    personnes, enriched = build_golden_records(_mdm_frame())
    assert len(personnes) == 4  # 7 comptes -> 4 personnes
    ids = dict(zip(enriched["id_client_uni"], enriched["id_personne_mdm"], strict=True))
    assert ids["ab1"] == ids["ap1"] and ids["ab3"] == ids["ap3"] and ids["ap4"] != ids["ab1"]
    unique = personnes[personnes["regle_rapprochement"] == "UNIQUE"].iloc[0]
    assert unique["nb_comptes_lies"] == 1 and unique["score_confiance"] == 1.0
    again, _ = build_golden_records(_mdm_frame())
    assert set(again["id_personne_mdm"]) == set(personnes["id_personne_mdm"])  # déterministe


def test_find_links_without_candidates():
    df = _mdm_frame().iloc[[0, 6]]
    assert find_links(df).empty
