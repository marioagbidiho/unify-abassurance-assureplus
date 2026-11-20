"""UNIFY - Plateforme Data & IA unifiée AbAssurance / AssurePlus.

Chaîne implémentée (cf. DFD niveau 1 du Bloc 1) :

    BDD X Oracle ─┐
                  ├─ P1 Talend/CDC → P2 Kafka → P3/P4 Spark → Hadoop (BRONZE/SILVER/GOLD)
    BDD Y MSSQL ──┘   → P5 MDM → P6 features → P7 modèle → P8 API de prédiction
"""

__version__ = "1.0.1"
