"""Définition du jeu de features de la prédiction de résiliation (gold.ft_client_resiliation).

Règles anti-fuite de données (data leakage) :
* toutes les features sont calculées avec des données ANTÉRIEURES OU ÉGALES à la date
  d'observation T ;
* ``date_fin`` et ``statut_contrat`` ne sont jamais des features : en cas de résiliation,
  la source met à jour la date de fin -> ce serait « lire la réponse » ;
* ``score_fraude_source`` (AP_FRAUD_SCORE) est exclu : score d'un outil tiers, absent côté AB ;
* aucune donnée directement identifiante (nom, e-mail, téléphone, n° fiscal).
"""

from __future__ import annotations

ID_COLUMNS = ["id_client_uni", "id_personne_mdm", "source_systeme", "date_observation"]
TARGET = "cible_resiliation_90j"

FEATURES = [
    "source_ap",
    "age",
    "anciennete_mois",
    "nb_contrats_actifs",
    "nb_produits_distincts",
    "prime_annuelle_totale",
    "a_produit_auto",
    "a_produit_habitation",
    "a_produit_sante",
    "nb_sinistres_12m",
    "montant_sinistres_12m",
    "nb_sinistres_refuses_12m",
    "nb_paiements_12m",
    "nb_paiements_rejetes_12m",
    "taux_paiement_12m",
    "jours_depuis_dernier_paiement",
    "score_fidelite",
    "fidelite_renseignee",
    "nb_comptes_lies",
]

FEATURE_LABELS = {
    "source_ap": "Client issu d'AssurePlus",
    "age": "Âge",
    "anciennete_mois": "Ancienneté (mois)",
    "nb_contrats_actifs": "Nombre de contrats actifs",
    "nb_produits_distincts": "Nombre de produits différents",
    "prime_annuelle_totale": "Prime annuelle totale (€)",
    "a_produit_auto": "Détient un contrat auto",
    "a_produit_habitation": "Détient un contrat habitation",
    "a_produit_sante": "Détient un contrat santé",
    "nb_sinistres_12m": "Sinistres sur 12 mois",
    "montant_sinistres_12m": "Montant des sinistres sur 12 mois (€)",
    "nb_sinistres_refuses_12m": "Sinistres refusés sur 12 mois",
    "nb_paiements_12m": "Paiements sur 12 mois",
    "nb_paiements_rejetes_12m": "Paiements rejetés sur 12 mois",
    "taux_paiement_12m": "Taux de paiement des primes sur 12 mois",
    "jours_depuis_dernier_paiement": "Jours depuis le dernier paiement",
    "score_fidelite": "Score de fidélité (AP)",
    "fidelite_renseignee": "Score de fidélité renseigné",
    "nb_comptes_lies": "Comptes liés (client des 2 compagnies)",
}

FORBIDDEN_FEATURES = {
    "date_fin",
    "statut_contrat",
    "score_fraude_source",
    "nom",
    "prenom",
    "email_hash",
    "telephone_chiffre",
    "num_fiscal_chiffre",
}
