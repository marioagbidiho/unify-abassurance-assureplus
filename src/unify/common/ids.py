"""Génération des clés techniques du modèle canonique (id_*_uni)."""

from __future__ import annotations

import uuid

SOURCE_SYSTEMS = ("AB", "AP")
# Espace de noms UUID propre au projet UNIFY (constante : ne jamais la modifier)
UNIFY_NAMESPACE = uuid.UUID("a3d5c1e2-7b4f-4e8a-9c2d-1f0e6b5a4c3d")


def canonical_id(entity: str, source_system: str, source_id: object) -> str:
    """Retourne la clé technique UUID d'un enregistrement canonique.

    Les deux sources ont des identifiants qui peuvent entrer en collision
    (AB_CLIENT_ID = 42 et AP_USER_ID = 42) : on attribue donc une clé UUID
    propre au modèle unifié et on conserve (source_systeme, id_source).

    La clé est DÉTERMINISTE (UUID v5) : la même ligne source produit toujours la même clé,
    ce qui rend les traitements idempotents (rejeu Kafka, reprise après incident).
    """
    if source_system not in SOURCE_SYSTEMS:
        raise ValueError(f"source_systeme inconnu : {source_system!r}")
    if source_id is None or str(source_id).strip() == "":
        raise ValueError(f"id_source vide pour {entity}/{source_system}")
    return str(uuid.uuid5(UNIFY_NAMESPACE, f"{entity}|{source_system}|{str(source_id).strip()}"))
