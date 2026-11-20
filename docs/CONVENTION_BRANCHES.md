# Convention de nommage des branches et de gestion du dépôt

Le dépôt suit le modèle **Git Flow**, adapté à une équipe Scrum (sprints de 2 semaines,
cf. Bloc 2). Les identifiants `UNI-nn` sont ceux des User Stories du tableau Trello.

## Branches permanentes

| Branche | Rôle | Règles |
|---|---|---|
| `main` | Code en **production**. Chaque commit de `main` est une version taguée `vX.Y.Z`. | Protégée : aucun push direct ; fusion uniquement depuis `release/*` ou `hotfix/*` par *pull request* ; CI verte + Quality Gate SonarCloud + 1 relecture obligatoires. |
| `develop` | Branche d'**intégration** du sprint en cours ; déployée en recette (REC). | Protégée : fusion par *pull request* depuis `feature/*`, `fix/*`, `docs/*`… ; CI verte obligatoire. |

## Branches temporaires

Format : `<type>/UNI-<n°>-<description-courte-en-kebab-case>` (minuscules, sans accents,
5 mots maximum). Exemple : `feature/UNI-4-standardisation-qualite`.

| Préfixe | Créée depuis | Fusionnée dans | Usage |
|---|---|---|---|
| `feature/` | `develop` | `develop` | Nouvelle fonctionnalité (une User Story) |
| `fix/` | `develop` | `develop` | Correction d'un bug détecté en DEV/REC |
| `hotfix/` | `main` | `main` **et** `develop` | Correction urgente d'un bug en production (version `X.Y.Z+1`) |
| `release/` | `develop` | `main` **et** `develop` | Stabilisation d'une version : `release/v1.0.0` |
| `docs/` | `develop` | `develop` | Documentation seule |
| `test/` | `develop` | `develop` | Ajout / refonte de tests |
| `chore/` | `develop` | `develop` | Outillage, CI, dépendances |

Les branches temporaires sont supprimées après fusion. Fusion en `--no-ff` pour conserver
la trace de chaque User Story dans l'historique.

## Messages de commit - Conventional Commits

```
<type>(<portée>): <résumé à l'impératif, < 72 caractères>

<corps : pourquoi le changement, quoi>

Refs: UNI-<n°>
```

Types : `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `ci`.
Portées : `ingestion`, `kafka`, `mapping`, `qualite`, `mdm`, `features`, `modele`, `api`, `app`, `ci`.
Exemple : `fix(mapping): clé id_paiement_uni déterministe (uuid5)`.

## Versions (SemVer) et tags

`vMAJEUR.MINEUR.CORRECTIF` : MAJEUR = rupture de contrat (schéma canonique, API `/v{n}`),
MINEUR = fonctionnalité compatible, CORRECTIF = correction de bug (hotfix).

## Pull request : Definition of Done (rappel Bloc 2)

- [ ] CI verte : ruff, bandit, tests unitaires et d'intégration Spark
- [ ] Couverture du code ≥ 80 % (seuil bloquant `fail_under = 80`)
- [ ] Quality Gate SonarCloud « Passed » (aucun bug / vulnérabilité bloquant ou critique)
- [ ] Relecture par au moins un pair
- [ ] Documentation et `config/mapping.yaml` à jour si les règles changent
