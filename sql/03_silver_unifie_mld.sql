-- =============================================================================
-- MLD / MPD de la base finale unifiée (zone SILVER du data lake Hadoop)
-- Dérivé du MCD docs/img/mcd_unify.png - moteur : Hive / Impala sur tables Iceberg
-- Règles de passage MCD -> MLD :
--   * association (1,1)-(0,n)  -> clé étrangère côté (1,1)   ex. CONTRAT.id_client_uni
--   * association (1,1)-(1,n)  -> clé étrangère côté (1,1)   ex. CLIENT.id_personne_mdm
--   * entités référentielles   -> tables ref_*               ex. ref_produit
-- Les contraintes PK/FK sont déclaratives (NOT ENFORCED) : elles sont vérifiées par
-- le contrôle qualité P4 (règles CLE_ABSENTE et INTEGRITE_REFERENTIELLE).
-- =============================================================================

CREATE DATABASE IF NOT EXISTS silver;

-- ------------------------------------------------------------------ référentiels
CREATE TABLE IF NOT EXISTS silver.ref_systeme_source (
    code_systeme   STRING  COMMENT 'AB | AP',
    libelle        STRING,
    sgbd           STRING  COMMENT 'Oracle | SQL Server',
    PRIMARY KEY (code_systeme) DISABLE NOVALIDATE
) STORED BY ICEBERG;

CREATE TABLE IF NOT EXISTS silver.ref_produit (
    code_uni        STRING  COMMENT 'AUTO, HABITATION, SANTE, PRO, ASSISTANCE',
    libelle         STRING,
    code_source_ab  STRING  COMMENT 'codes AB_TYPE_ASSURANCE séparés par |',
    code_source_ap  STRING  COMMENT 'codes AP_PRODUCT_CODE séparés par |',
    domaine         STRING,
    PRIMARY KEY (code_uni) DISABLE NOVALIDATE
) STORED BY ICEBERG;

-- ref_statut_client, ref_statut_contrat, ref_statut_sinistre, ref_moyen_paiement,
-- ref_statut_transaction : même structure que ref_produit (config/referentiels.yaml)

CREATE TABLE IF NOT EXISTS silver.ref_distributeur (
    id_distributeur     STRING,
    canal_distribution  STRING  COMMENT 'AGENCE (AB_AGENCE_ID) | COURTIER (AP_BROKER_CODE)',
    PRIMARY KEY (id_distributeur, canal_distribution) DISABLE NOVALIDATE
) STORED BY ICEBERG;

-- ------------------------------------------------------------------ MDM
CREATE TABLE IF NOT EXISTS silver.mdm_personne (
    id_personne_mdm      STRING        COMMENT 'UUID v5 déterministe du golden record',
    regle_rapprochement  STRING        COMMENT 'R1_EMAIL_DDN | R2_IDENTITE | R3_PROBABILISTE | UNIQUE',
    score_confiance      DECIMAL(5,2),
    nb_comptes_lies      INT,
    PRIMARY KEY (id_personne_mdm) DISABLE NOVALIDATE
) STORED BY ICEBERG;

-- ------------------------------------------------------------------ données métier
CREATE TABLE IF NOT EXISTS silver.uni_client (
    id_client_uni       STRING     COMMENT 'PK - UUID v5(client|source|id_source)',
    id_personne_mdm     STRING     COMMENT 'FK -> mdm_personne',
    id_client_source    STRING     COMMENT 'AB_CLIENT_ID | AP_USER_ID',
    nom                 STRING     COMMENT 'masqué par Ranger hors rôle habilité',
    prenom              STRING,
    date_naissance      DATE,
    email_hash          STRING     COMMENT 'HMAC-SHA256 de l e-mail normalisé',
    telephone_chiffre   STRING     COMMENT 'E.164 chiffré AES-256-GCM',
    adresse             STRING,
    code_postal         STRING,
    pays                STRING,
    num_fiscal_chiffre  STRING     COMMENT 'AB uniquement, chiffré, interdit pour l IA',
    date_creation_utc   TIMESTAMP,
    statut_client       STRING     COMMENT 'FK -> ref_statut_client',
    score_fidelite      INT        COMMENT 'AP uniquement (NULL pour AB)',
    source_systeme      STRING     COMMENT 'FK -> ref_systeme_source',
    dt_ingestion_utc    TIMESTAMP,
    hash_ligne          STRING,
    anomalies           STRING,
    PRIMARY KEY (id_client_uni) DISABLE NOVALIDATE,
    FOREIGN KEY (id_personne_mdm) REFERENCES silver.mdm_personne (id_personne_mdm) DISABLE NOVALIDATE
) STORED BY ICEBERG;

CREATE TABLE IF NOT EXISTS silver.uni_contrat (
    id_contrat_uni      STRING        COMMENT 'PK',
    id_client_uni       STRING        COMMENT 'FK -> uni_client (association SOUSCRIRE)',
    ref_contrat_source  STRING        COMMENT 'AB_POLICY_NUMBER | AP_CONTRACT_REF',
    code_produit_uni    STRING        COMMENT 'FK -> ref_produit (PORTER_SUR)',
    date_debut          DATE,
    date_fin            DATE,
    prime_annuelle_eur  DECIMAL(12,2) COMMENT 'AP : AP_MONTHLY_PREMIUM x 12',
    periodicite_source  STRING        COMMENT 'ANNUELLE | MENSUELLE',
    statut_contrat      STRING        COMMENT 'FK -> ref_statut_contrat',
    canal_distribution  STRING,
    id_distributeur     STRING        COMMENT 'FK -> ref_distributeur (DISTRIBUER)',
    source_systeme      STRING,
    dt_ingestion_utc    TIMESTAMP,
    hash_ligne          STRING,
    anomalies           STRING,
    PRIMARY KEY (id_contrat_uni) DISABLE NOVALIDATE,
    FOREIGN KEY (id_client_uni) REFERENCES silver.uni_client (id_client_uni) DISABLE NOVALIDATE
) STORED BY ICEBERG;

CREATE TABLE IF NOT EXISTS silver.uni_sinistre (
    id_sinistre_uni          STRING        COMMENT 'PK',
    id_contrat_uni           STRING        COMMENT 'FK -> uni_contrat (CONCERNER)',
    id_sinistre_source       STRING        COMMENT 'AB_CLAIM_ID | AP_SINISTRE_NUM',
    date_heure_sinistre_utc  TIMESTAMP,
    montant_estime_eur       DECIMAL(14,2),
    statut_sinistre          STRING,
    description_texte        STRING        COMMENT 'données personnelles masquées',
    score_fraude_source      DECIMAL(5,2)  COMMENT 'AP uniquement - jamais utilisé comme label',
    source_systeme           STRING,
    dt_ingestion_utc         TIMESTAMP,
    hash_ligne               STRING,
    anomalies                STRING,
    PRIMARY KEY (id_sinistre_uni) DISABLE NOVALIDATE,
    FOREIGN KEY (id_contrat_uni) REFERENCES silver.uni_contrat (id_contrat_uni) DISABLE NOVALIDATE
) PARTITIONED BY SPEC (years(date_heure_sinistre_utc)) STORED BY ICEBERG;

CREATE TABLE IF NOT EXISTS silver.uni_paiement (
    id_paiement_uni          STRING        COMMENT 'PK - UUID v5 déterministe (cf. correctif UNI-42)',
    id_contrat_uni           STRING        COMMENT 'FK -> uni_contrat (RÉGLER)',
    id_paiement_source       STRING        COMMENT 'AB_PAYMENT_ID (NUMBER) | AP_PAYMENT_REF (GUID)',
    date_heure_paiement_utc  TIMESTAMP,
    montant_eur              DECIMAL(12,2),
    moyen_paiement           STRING        COMMENT 'FK -> ref_moyen_paiement (UTILISER)',
    statut_transaction       STRING        COMMENT 'NON_RENSEIGNE pour AB',
    source_systeme           STRING,
    dt_ingestion_utc         TIMESTAMP,
    hash_ligne               STRING,
    anomalies                STRING,
    PRIMARY KEY (id_paiement_uni) DISABLE NOVALIDATE,
    FOREIGN KEY (id_contrat_uni) REFERENCES silver.uni_contrat (id_contrat_uni) DISABLE NOVALIDATE
) PARTITIONED BY SPEC (months(date_heure_paiement_utc)) STORED BY ICEBERG;
