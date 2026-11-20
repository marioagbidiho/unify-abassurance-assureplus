"""Non-régression UNI-42 : le pipeline doit être idempotent.

Kafka garantit une livraison « at-least-once » : après un redémarrage du consommateur, des
messages déjà traités peuvent être relivrés. Rejouer les mêmes événements ne doit JAMAIS
changer le contenu de SILVER (aucun doublon, mêmes clés).
"""

import inspect

import pandas as pd
import pytest

from unify.mapping import canonical
from unify.quality import apply_quality


@pytest.mark.parametrize("table", list(canonical.MAPPERS))
def test_mapping_twice_gives_same_keys(table, small_tables, settings):
    entity, mapper = canonical.MAPPERS[table]
    key = f"id_{entity}_uni"
    raw = small_tables[table].head(50)
    first, second = mapper(raw, settings), mapper(raw, settings)
    assert first[key].tolist() == second[key].tolist()


@pytest.mark.parametrize("table", ["AB_PAIEMENT", "AP_PAYMENTS"])
def test_kafka_redelivery_does_not_duplicate_payments(table, small_tables, settings):
    raw = small_tables[table]
    entity, mapper = canonical.MAPPERS[table]
    once = apply_quality(mapper(raw, settings), entity)
    redelivered = apply_quality(mapper(pd.concat([raw, raw], ignore_index=True), settings), entity)
    assert len(redelivered.valid) == len(once.valid)
    assert redelivered.report["nb_doublons_supprimes"] >= len(raw)


def test_source_duplicates_are_removed(small_tables, settings):
    raw = small_tables["AB_PAIEMENT"]
    expected = raw["AB_PAYMENT_ID"].nunique()
    result = apply_quality(canonical.map_ab_paiement(raw, settings), "paiement")
    assert len(result.valid) + len(result.rejects) == expected


def test_no_random_key_in_mapping_module():
    """Règle d'architecture : aucune clé technique aléatoire dans le mapping."""
    assert "uuid4" not in inspect.getsource(canonical)
