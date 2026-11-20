# Rapport d'anomalie UNI-42 – doublons de paiements au rejeu Kafka

| Champ | Valeur |
|---|---|
| Sévérité | **Majeure** (données fausses en SILVER/GOLD, sans erreur visible) |
| Composant | `unify.mapping.canonical._paiement` (processus P3) |
| Version affectée | v1.0.0 |
| Corrigée dans | v1.0.1 (branche `hotfix/UNI-42-idempotence-paiements`) |

## Symptôme
* Le rapport qualité indique `nb_doublons_supprimes = 0` pour les paiements alors que la
  source `AB_PAIEMENT` contient des lignes techniques en double (même `AB_PAYMENT_ID`).
* Après une re-livraison Kafka (consommateur redémarré avant le commit des offsets :
  garantie *at-least-once*), la table `silver.uni_paiement` contient **deux fois** les paiements
  rejoués : `nb_paiements_12m` et `taux_paiement_12m` sont gonflés → le modèle voit des
  clients « bons payeurs » qui ne le sont pas.

## Cause racine (méthode des 5 pourquoi)
1. Pourquoi des doublons en SILVER ? Le dédoublonnage par clé ne les fusionne pas.
2. Pourquoi ? Les deux copies d'un même paiement ont deux `id_paiement_uni` différents.
3. Pourquoi ? La clé est générée par `uuid.uuid4()` (aléatoire) à chaque exécution.
4. Pourquoi ? Le développeur a jugé que les paiements, sans table fille, n'avaient pas besoin
   d'une clé stable (« AP_PAYMENT_REF est déjà un GUID »).
5. Pourquoi cela n'a-t-il pas été détecté ? Aucun test ne vérifiait l'**idempotence** (rejouer
   = même résultat), alors que la spécification `mapping.yaml` exigeait une clé `uuid5`.

## Correctif
`id_paiement_uni = canonical_id("paiement", source, id_source)` : UUID v5 déterministe,
comme pour les autres entités (cohérence avec la spécification).

## Validation
* Test de non-régression `tests/test_idempotence.py` : écrit **avant** le correctif, il échoue
  sur v1.0.0 (TDD « rouge ») puis passe après correctif (« vert »).
* Test d'intégration Spark : `unify run --redeliver AB_PAIEMENT AP_PAYMENTS` → nombre de
  paiements SILVER identique à l'exécution sans re-livraison.
* Réconciliation : `lues = valides + quarantaine + doublons supprimés`.

## Prévention
* Règle d'architecture : **toute clé technique est déterministe** (aucun `uuid4` dans le
  mapping) – vérifiée par un test qui scanne le module.
* Ajout d'un contrôle de réconciliation source ↔ SILVER dans le DAG (alerte si écart > 0,1 %).
