# Jobs Talend - processus P1 (chargement initial) et P3 (standardisation)

Talend Studio (Qlik Talend Cloud) est l'outil ETL imposé par la direction technique.
Les jobs sont versionnés dans ce dossier (export `.item` / `.properties` depuis le Studio,
référentiel Git du projet Talend). Ce document décrit leur conception ; les **règles de
transformation** ne sont écrites qu'une seule fois, dans `config/mapping.yaml`, et sont
reprises à l'identique par le tMap Talend et par le code Python/Spark (`unify.mapping`).

## Contexte (Context Group `CTX_UNIFY`)

| Variable | DEV | PRD |
|---|---|---|
| `ab_jdbc_url` | `jdbc:oracle:thin:@oracle:1521/FREEPDB1` | via coffre (Vault) |
| `ap_jdbc_url` | `jdbc:sqlserver://sqlserver:1433;databaseName=ASSUREPLUS;encrypt=true` | via coffre |
| `kafka_bootstrap` | `kafka:9092` | `dc1-prd-kfk-01:9093,...` (TLS) |
| `schema_registry` | `http://schema-registry:8081` | `https://...` |
| `env` | `dev` | `prd` |
| `batch_size` | `50000` | `50000` |

## Jobs

| Job | Rôle | Composants principaux | Sortie |
|---|---|---|---|
| `J01_INIT_AB_<TABLE>` (x4) | Chargement initial Oracle, par lots, hors heures ouvrées, sur réplica | `tDBConnection (Oracle)` → `tDBInput` (requête paginée par ROWID) → `tMap` (enveloppe CDC `op=r`) → `tKafkaOutput` (Avro) | topic `{env}.ab.raw.{entité}.v1` |
| `J02_INIT_AP_<TABLE>` (x4) | Chargement initial SQL Server | `tDBInput (MSSQL)` → `tMap` → `tKafkaOutput` | topic `{env}.ap.raw.{entité}.v1` |
| `J03_RECONCILIATION` | Contrôle comptages source = topic = BRONZE | `tDBInput` (COUNT) + `tFileInputParquet` → `tAssert` | rapport de réconciliation |
| `J10_STD_<ENTITE>` (option) | Standardisation canonique pour les volumes moyens | `tKafkaInput` → `tMap` (règles `mapping.yaml`) → `tSchemaComplianceCheck` → `tHDFSOutput (Parquet)` + flux rejet → `/data/quarantaine` | `silver.uni_*` |

> La synchronisation continue (CDC) n'est **pas** faite par Talend mais par Debezium
> (`config/debezium/*.json`) : lecture des journaux Oracle (LogMiner) et SQL Server (CDC),
> sans aucune modification des applications sources (exigence EF-01 / RSQ-01).

## Règles tMap (extrait) - identiques à `config/mapping.yaml`

| Source | Expression Talend | Cible |
|---|---|---|
| `AP_MONTHLY_PREMIUM` | `row.AP_MONTHLY_PREMIUM.multiply(new BigDecimal(12))` | `prime_annuelle_eur` |
| `AP_BIRTH_DATE` | `TalendDate.parseDate("yyyy-MM-dd", row.AP_BIRTH_DATE)` + rejet si exception | `date_naissance` |
| `AP_INCIDENT_DATE` | `TalendDate.parseDate("yyyy-MM-dd HH:mm:ss", ...)` puis conversion UTC | `date_heure_sinistre_utc` |
| `AB_TYPE_ASSURANCE` | `tMap` lookup sur `ref_produit` (jointure *inner* → rejet si inconnu) | `code_produit_uni` |
| `AB_EMAIL` / `AP_MAIL_ADDRESS` | `StringHandling.DOWNCASE(TRIM(...))` puis `tDataMasking` HMAC | `email_hash` |

## Équivalent exécutable

Dans la CI et la démonstration, ces jobs sont remplacés par leur équivalent Python
(`unify.ingestion.extract` + `unify.transport.bus`) afin que le pipeline soit testable sans
licence Talend ; le contrat (topics, format d'enveloppe, règles) est strictement le même.
