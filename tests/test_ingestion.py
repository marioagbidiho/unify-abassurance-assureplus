"""Tests du générateur de sources, de l'extraction (P1), du bus Kafka (P2) et du lac."""

from datetime import date, datetime

import pandas as pd
import pytest

from unify.config import Settings
from unify.ingestion.extract import SOURCE_TABLES, extract_table, to_cdc_events
from unify.lake import Lake
from unify.sources.generator import GeneratorConfig, generate, write_sqlite
from unify.transport.bus import InMemoryBus, get_bus

ANNEX_COLUMNS = {
    "AB_CLIENT": [
        "AB_CLIENT_ID",
        "AB_NOM",
        "AB_PRENOM",
        "AB_DATE_NAISSANCE",
        "AB_EMAIL",
        "AB_TELEPHONE",
        "AB_ADRESSE",
        "AB_CODE_POSTAL",
        "AB_NUM_FISCAL",
        "AB_DATE_CREATION",
        "AB_STATUT_CLIENT",
    ],
    "AB_CONTRAT": [
        "AB_POLICY_NUMBER",
        "AB_CLIENT_ID",
        "AB_TYPE_ASSURANCE",
        "AB_DATE_DEBUT",
        "AB_DATE_FIN",
        "AB_PRIME_ANNUELLE",
        "AB_STATUT_CONTRAT",
        "AB_AGENCE_ID",
    ],
    "AB_SINISTRE": [
        "AB_CLAIM_ID",
        "AB_POLICY_NUMBER",
        "AB_DATE_SINISTRE",
        "AB_MONTANT_ESTIME",
        "AB_STATUT_SINISTRE",
        "AB_DESCRIPTION",
    ],
    "AB_PAIEMENT": [
        "AB_PAYMENT_ID",
        "AB_POLICY_NUMBER",
        "AB_DATE_PAIEMENT",
        "AB_MONTANT",
        "AB_MODE_PAIEMENT",
    ],
    "AP_USERS": [
        "AP_USER_ID",
        "AP_FULL_NAME",
        "AP_BIRTH_DATE",
        "AP_MAIL_ADDRESS",
        "AP_PHONE_NUMBER",
        "AP_STREET_ADDRESS",
        "AP_ZIP_CODE",
        "AP_CREATED_AT",
        "AP_CUSTOMER_STATUS",
        "AP_LOYALTY_SCORE",
    ],
    "AP_CONTRACTS": [
        "AP_CONTRACT_REF",
        "AP_USER_ID",
        "AP_PRODUCT_CODE",
        "AP_START_DATE",
        "AP_END_DATE",
        "AP_MONTHLY_PREMIUM",
        "AP_CONTRACT_STATE",
        "AP_BROKER_CODE",
    ],
    "AP_CLAIMS": [
        "AP_SINISTRE_NUM",
        "AP_CONTRACT_REF",
        "AP_INCIDENT_DATE",
        "AP_ESTIMATED_AMOUNT",
        "AP_CLAIM_STATUS",
        "AP_CLAIM_COMMENT",
        "AP_FRAUD_SCORE",
    ],
    "AP_PAYMENTS": [
        "AP_PAYMENT_REF",
        "AP_CONTRACT_REF",
        "AP_PAYMENT_DATETIME",
        "AP_AMOUNT_PAID",
        "AP_PAYMENT_CHANNEL",
        "AP_TRANSACTION_STATUS",
    ],
}


def test_generator_matches_annex_schemas(small_tables):
    for table, columns in ANNEX_COLUMNS.items():
        assert list(small_tables[table].columns) == columns, table


def test_generator_is_reproducible():
    cfg = GeneratorConfig(n_ab=30, n_ap=15, overlap=5, seed=3)
    first, second = generate(cfg), generate(cfg)
    for table in first:
        pd.testing.assert_frame_equal(first[table], second[table])


def test_generator_injects_known_defects(small_tables):
    births = small_tables["AP_USERS"]["AP_BIRTH_DATE"]
    assert births.map(type).eq(str).all()  # date en texte côté AP
    assert small_tables["AP_CLAIMS"]["AP_INCIDENT_DATE"].map(type).eq(str).all()
    shared = set(small_tables["AB_CLIENT"]["AB_NOM"].str.upper().head(15))
    assert shared  # clients communs
    assert small_tables["AB_CONTRAT"]["AB_STATUT_CONTRAT"].nunique() > 2  # casse hétérogène


@pytest.fixture
def sources(settings, small_tables, tmp_path):
    paths = write_sqlite(small_tables, tmp_path / "ab.db", tmp_path / "ap.db")
    return Settings(
        **{
            **settings.__dict__,
            "ab_source_url": f"sqlite:///{paths['AB']}",
            "ap_source_url": f"sqlite:///{paths['AP']}",
        }
    )


def test_extract_all_tables_in_chunks(sources, small_tables):
    for source, tables in SOURCE_TABLES.items():
        url = sources.ab_source_url if source == "AB" else sources.ap_source_url
        for table in tables:
            chunks = list(extract_table(url, table, chunksize=50))
            assert sum(len(c) for c in chunks) == len(small_tables[table])


def test_incremental_extraction_with_watermark(sources, small_tables):
    since = datetime(2020, 1, 1)
    rows = pd.concat(extract_table(sources.ab_source_url, "AB_CLIENT", since=since))
    expected = (pd.to_datetime(small_tables["AB_CLIENT"]["AB_DATE_CREATION"]) > since).sum()
    assert 0 < len(rows) == expected < len(small_tables["AB_CLIENT"])


def test_extract_rejects_unknown_table(sources):
    with pytest.raises(ValueError, match="non autorisée"):
        next(extract_table(sources.ab_source_url, "AB_CLIENT; DROP TABLE AB_CLIENT"))


def test_cdc_envelope():
    df = pd.DataFrame(
        [
            {
                "ID": 1,
                "D": date(2026, 1, 2),
                "T": datetime(2026, 1, 2, 3, 4, 5),
                "X": float("nan"),
                "N": pd.Series([5]).iloc[0],
            }
        ]
    )
    event = to_cdc_events(df, "AB", "AB_CLIENT")[0]
    assert event["op"] == "r" and event["before"] is None
    assert event["after"] == {
        "ID": 1,
        "D": "2026-01-02",
        "T": "2026-01-02 03:04:05",
        "X": None,
        "N": 5,
    }
    assert event["source"] == {"system": "AB", "table": "AB_CLIENT", "connector": "oracle"}
    assert to_cdc_events(df, "AP", "AP_USERS", op="d")[0]["after"] is None


def test_in_memory_bus_offsets_and_replay():
    bus = InMemoryBus()
    assert bus.publish("t", [{"a": 1}, {"a": 2}]) == 2
    assert [m["a"] for m in bus.consume("t")] == [1, 2]
    assert list(bus.consume("t")) == []  # offsets engagés
    bus.publish("t", [{"a": 3}])
    assert [m["a"] for m in bus.consume("t")] == [3]
    bus.rewind("t")
    assert len(list(bus.consume("t"))) == 3  # rejeu complet
    assert list(bus.consume("absent")) == []


def test_get_bus_and_topic_naming(settings):
    assert isinstance(get_bus(settings), InMemoryBus)
    assert settings.topic("ab", "raw", "contrat") == "test.ab.raw.contrat.v1"


def test_lake_paths_and_io(tmp_path):
    lake = Lake(str(tmp_path))
    assert lake.bronze("AB", "client", date(2026, 3, 1)).endswith(
        "/data/bronze/ab/client/date_ingestion=2026-03-01"
    )
    assert lake.silver("client").endswith("/data/silver/uni_client")
    assert lake.mdm().endswith("/data/silver/mdm_personne")
    assert lake.quarantine("paiement", date(2026, 3, 1)).endswith(
        "/data/quarantaine/paiement/date_ingestion=2026-03-01"
    )
    assert lake.gold("ft_x").endswith("/data/gold/ft_x")
    Lake.write(pd.DataFrame({"a": [1, 2]}), lake.gold("ft_x"))
    assert Lake.read(lake.gold("ft_x"))["a"].tolist() == [1, 2]
    assert Lake.read(lake.gold("absent")).empty
