"""Générateur de données sources synthétiques (BDD X Oracle AB_* et BDD Y SQL Server AP_*).

Les vraies bases contiennent des données personnelles : elles ne peuvent pas être utilisées
en développement ni publiées sur Git (RGPD). Ce module produit deux bases SQLite qui
respectent EXACTEMENT les schémas de l'annexe (noms et types de colonnes) et reproduisent
les défauts réels observés au Bloc 1 (§ 1.3) :

* dates stockées en texte côté AP, formats mixtes et valeurs impossibles ;
* casse et libellés de statuts hétérogènes, codes produits différents ;
* e-mails / téléphones / codes postaux mal formatés ;
* clients présents dans les deux compagnies sans clé commune (≈ 10 % du portefeuille AP) ;
* doublons de paiements, montants négatifs, score de fidélité hors bornes.

Un comportement de résiliation latent (retards de paiement, sinistres refusés, ancienneté,
mono-équipement…) est simulé pour que le modèle ait un signal réaliste à apprendre.
Échelle par défaut : 1/2 000 du portefeuille réel (6 000 clients AB, 2 500 clients AP).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from unify.common.text import strip_accents

PRENOMS = [
    "Jean",
    "Marie",
    "Pierre",
    "Sophie",
    "Luc",
    "Camille",
    "Nicolas",
    "Julie",
    "Thomas",
    "Léa",
    "Antoine",
    "Chloé",
    "Hugo",
    "Emma",
    "Louis",
    "Inès",
    "Mathieu",
    "Sarah",
    "Jean-Pierre",
    "Anne-Sophie",
    "Karim",
    "Fatou",
    "Mamadou",
    "Aïcha",
    "Yann",
    "Élodie",
    "François",
    "Hélène",
    "Mario",
    "Lucas",
]
NOMS = [
    "Martin",
    "Bernard",
    "Dubois",
    "Thomas",
    "Robert",
    "Richard",
    "Petit",
    "Durand",
    "Leroy",
    "Moreau",
    "Simon",
    "Laurent",
    "Lefèvre",
    "Michel",
    "Garcia",
    "David",
    "Bertrand",
    "Roux",
    "Vincent",
    "Fournier",
    "Morel",
    "Girard",
    "André",
    "Mercier",
    "Diallo",
    "Nguyen",
    "Agbidiho",
    "Kone",
    "Blanc",
    "Guérin",
    "Muller",
    "Faure",
]
VILLES_CP = [
    "75011",
    "69003",
    "13008",
    "31000",
    "06000",
    "33000",
    "44000",
    "59000",
    "67000",
    "35000",
    "01000",
    "34000",
    "21000",
    "76000",
    "05000",
    "38000",
]
RUES = [
    "rue de la République",
    "avenue Victor Hugo",
    "boulevard Voltaire",
    "rue Nationale",
    "place de la Mairie",
    "chemin des Vignes",
    "allée des Tilleuls",
]
DOMAINES = ["gmail.com", "orange.fr", "free.fr", "yahoo.fr", "outlook.com"]

AB_PRODUITS = ["AUTO", "Auto", "HABITATION", "habitation", "MRH", "SANTE", "Santé", "PRO"]
AP_PRODUITS = ["AUTO_STD", "AUTO_PREMIUM", "ASSIST_PREMIUM", "AUTO_STD"]
AB_MODES = ["PRELEVEMENT", "Prélèvement", "CB", "VIREMENT", "CHEQUE"]
AP_CANAUX = ["DIRECT_DEBIT", "CARD", "MOBILE_APP", "TRANSFER"]


@dataclass(frozen=True)
class GeneratorConfig:
    n_ab: int = 6000
    n_ap: int = 2500
    overlap: int = 250
    seed: int = 42
    extraction_date: date = date(2026, 9, 30)
    snapshot_date: date = date(2026, 6, 30)
    horizon_days: int = 90


class _Gen:
    def __init__(self, cfg: GeneratorConfig):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)

    # ------------------------------------------------------------------ helpers
    def choice(self, seq):
        return seq[int(self.rng.integers(0, len(seq)))]

    def chance(self, p: float) -> bool:
        return bool(self.rng.random() < p)

    def rand_date(self, start: date, end: date) -> date:
        span = max((end - start).days, 1)
        return start + timedelta(days=int(self.rng.integers(0, span)))

    def rand_dt(self, start: date, end: date) -> datetime:
        d = self.rand_date(start, end)
        return datetime(
            d.year,
            d.month,
            d.day,
            int(self.rng.integers(7, 22)),
            int(self.rng.integers(0, 60)),
            int(self.rng.integers(0, 60)),
        )

    # ------------------------------------------------------------------ personnes
    def person(self, idx: int) -> dict:
        prenom, nom = self.choice(PRENOMS), self.choice(NOMS)
        age = int(self.rng.integers(19, 86))
        birth = date(
            self.cfg.snapshot_date.year - age,
            int(self.rng.integers(1, 13)),
            int(self.rng.integers(1, 29)),
        )
        mail_user = strip_accents(f"{prenom}.{nom}{idx % 97}").lower().replace(" ", "")
        return {
            "prenom": prenom,
            "nom": nom,
            "birth": birth,
            "age": age,
            "email": f"{mail_user}@{self.choice(DOMAINES)}",
            "phone": "0"
            + str(int(self.rng.integers(6, 8)))
            + "".join(str(int(x)) for x in self.rng.integers(0, 10, 8)),
            "adresse": f"{int(self.rng.integers(1, 200))} {self.choice(RUES)}",
            "cp": self.choice(VILLES_CP),
        }

    def churn_profile(self, source: str, person: dict) -> dict:
        """Facteurs de risque latents et tirage de la résiliation."""
        r = self.rng
        tenure_years = float(r.uniform(0.3, 20))
        n_contracts = int(r.choice([1, 1, 1, 2, 2, 3]))
        payment_issues = int(r.poisson(0.5))
        claims_12m = int(r.poisson(0.35))
        claim_refused = claims_12m > 0 and self.chance(0.25)
        loyalty = int(np.clip(r.normal(60, 20), 0, 100)) if source == "AP" else None
        premium_factor = float(r.lognormal(0, 0.35))
        logit = (
            -2.75
            + 1.05 * min(payment_issues, 4)
            + 1.7 * claim_refused
            + 0.35 * claims_12m
            - 0.7 * (n_contracts - 1)
            - 0.12 * min(tenure_years, 15)
            + 0.035 * max(0, 45 - person["age"])
            + 1.1 * (premium_factor > 1.3)
            + (0.6 - 0.04 * (loyalty - 60) if loyalty is not None else 0.0)
            + r.normal(0, 0.25)
        )
        churn = self.chance(1 / (1 + np.exp(-logit)))
        return {
            "tenure_years": tenure_years,
            "n_contracts": n_contracts,
            "payment_issues": payment_issues,
            "claims_12m": claims_12m,
            "claim_refused": claim_refused,
            "loyalty": loyalty,
            "premium_factor": premium_factor,
            "churn": churn,
            "gone_before": self.chance(0.04),
        }


def _status_ab(gen: _Gen, resilie: bool) -> str:
    if resilie:
        return gen.choice(["RESILIE", "Résilié", "RESILIE"])
    return gen.choice(["EN_COURS", "En cours", "ACTIF", "EN_COURS"])


def _status_ap(gen: _Gen, resilie: bool) -> str:
    if resilie:
        return gen.choice(["CANCELLED", "TERMINATED", "cancelled"])
    return gen.choice(["ACTIVE", "ACTIVE", "active", "IN_FORCE"])


def _end_and_status(gen: _Gen, prof: dict, start: date, src: str) -> tuple[date, bool]:
    cfg = gen.cfg
    if prof["gone_before"]:  # client parti avant T : hors population observée
        earliest = max(start + timedelta(days=30), date(2024, 1, 1))
        return gen.rand_date(earliest, cfg.snapshot_date - timedelta(days=1)), True
    if gen.chance(0.003):  # défaut de saisie volontaire : fin avant début
        return start - timedelta(days=int(gen.rng.integers(1, 400))), False
    if prof["churn"]:
        return cfg.snapshot_date + timedelta(days=int(gen.rng.integers(1, cfg.horizon_days))), True
    end = date(cfg.extraction_date.year + 1, start.month, min(start.day, 28))
    return end, False


def _payment_dates(start: date, end: date, step_months: int, until: date) -> list[date]:
    out, cursor = [], start
    while cursor <= min(end, until):
        if cursor >= date(2024, 7, 1):
            out.append(cursor)
        month = cursor.month - 1 + step_months
        cursor = date(cursor.year + month // 12, month % 12 + 1, min(cursor.day, 28))
    return out


def generate(cfg: GeneratorConfig | None = None) -> dict[str, pd.DataFrame]:  # noqa: C901
    """Génère les 8 tables sources. Retourne un dict {nom_table: DataFrame}."""
    cfg = cfg or GeneratorConfig()
    g = _Gen(cfg)
    tables: dict[str, list[dict]] = {
        k: []
        for k in (
            "AB_CLIENT",
            "AB_CONTRAT",
            "AB_SINISTRE",
            "AB_PAIEMENT",
            "AP_USERS",
            "AP_CONTRACTS",
            "AP_CLAIMS",
            "AP_PAYMENTS",
        )
    }
    shared_people = [g.person(i) for i in range(cfg.overlap)]
    claim_seq = {"AB": 500000, "AP": 900000}
    pay_seq = 7000000

    for src, n in (("AB", cfg.n_ab), ("AP", cfg.n_ap)):
        for i in range(n):
            # les `overlap` premiers clients de chaque base sont les mêmes personnes
            person = shared_people[i] if i < cfg.overlap else g.person(i + 13)
            prof = g.churn_profile(src, person)
            created = cfg.snapshot_date - timedelta(days=int(prof["tenure_years"] * 365))
            client_id = 10000 + i if src == "AB" else 20000 + i * 3

            # ---------------------------------------------------------- client
            if src == "AB":
                email = person["email"] if not g.chance(0.03) else person["email"].upper()
                if g.chance(0.01):
                    email = email.replace("@", " at ")
                phone = person["phone"]
                if g.chance(0.3):
                    phone = " ".join(phone[k : k + 2] for k in range(0, 10, 2))
                tables["AB_CLIENT"].append(
                    {
                        "AB_CLIENT_ID": client_id,
                        "AB_NOM": person["nom"].upper(),
                        "AB_PRENOM": person["prenom"]
                        if not g.chance(0.02)
                        else f" {person['prenom']} ",
                        "AB_DATE_NAISSANCE": person["birth"]
                        if not g.chance(0.003)
                        else date(1850, 1, 1),
                        "AB_EMAIL": email,
                        "AB_TELEPHONE": phone,
                        "AB_ADRESSE": person["adresse"],
                        "AB_CODE_POSTAL": person["cp"].lstrip("0")
                        if g.chance(0.05)
                        else person["cp"],
                        "AB_NUM_FISCAL": "".join(str(int(x)) for x in g.rng.integers(0, 10, 13)),
                        "AB_DATE_CREATION": datetime.combine(created, datetime.min.time())
                        + timedelta(hours=10),
                        "AB_STATUT_CLIENT": g.choice(["ACTIF", "A", "Actif"]),
                    }
                )
            else:
                fmt = g.rng.random()
                birth_txt = (
                    person["birth"].strftime("%d/%m/%Y")
                    if fmt < 0.02
                    else f"{person['birth'].year}-13-45"
                    if fmt < 0.03
                    else person["birth"].isoformat()
                )
                full_name = (
                    f"{person['prenom']} {person['nom'].upper()}"
                    if g.chance(0.6)
                    else f"{person['nom'].upper()}, {person['prenom']}"
                    if g.chance(0.5)
                    else f"{person['prenom']} {person['nom']}"
                )
                phone = "+33 " + person["phone"][1:] if g.chance(0.5) else person["phone"]
                tables["AP_USERS"].append(
                    {
                        "AP_USER_ID": client_id,
                        "AP_FULL_NAME": full_name,
                        "AP_BIRTH_DATE": birth_txt,
                        "AP_MAIL_ADDRESS": person["email"]
                        if g.chance(0.85)
                        else person["email"].replace("@", f"{i}@"),
                        "AP_PHONE_NUMBER": phone,
                        "AP_STREET_ADDRESS": person["adresse"].upper(),
                        "AP_ZIP_CODE": person["cp"],
                        "AP_CREATED_AT": datetime.combine(created, datetime.min.time())
                        + timedelta(hours=14),
                        "AP_CUSTOMER_STATUS": g.choice(["ACTIVE", "ACTIVE", "active"]),
                        "AP_LOYALTY_SCORE": prof["loyalty"] if not g.chance(0.005) else 999,
                    }
                )

            # ---------------------------------------------------------- contrats
            for k in range(prof["n_contracts"]):
                start = g.rand_date(
                    max(created, date(2012, 1, 1)), cfg.snapshot_date - timedelta(days=60)
                )
                end, resilie = _end_and_status(g, prof, start, src)
                if src == "AB":
                    ref = f"AB-{client_id}-{k + 1:02d}"
                    prime = round(float(g.rng.uniform(250, 1100)) * prof["premium_factor"], 2)
                    tables["AB_CONTRAT"].append(
                        {
                            "AB_POLICY_NUMBER": ref,
                            "AB_CLIENT_ID": client_id,
                            "AB_TYPE_ASSURANCE": g.choice(AB_PRODUITS),
                            "AB_DATE_DEBUT": start,
                            "AB_DATE_FIN": end,
                            "AB_PRIME_ANNUELLE": prime,
                            "AB_STATUT_CONTRAT": _status_ab(g, resilie),
                            "AB_AGENCE_ID": int(g.rng.integers(100, 160)),
                        }
                    )
                    step, amount = g.choice([3, 12, 1]), prime
                else:
                    ref = f"APC{client_id:07d}{k}"
                    monthly = round(float(g.rng.uniform(25, 95)) * prof["premium_factor"], 2)
                    tables["AP_CONTRACTS"].append(
                        {
                            "AP_CONTRACT_REF": ref,
                            "AP_USER_ID": client_id,
                            "AP_PRODUCT_CODE": g.choice(AP_PRODUITS),
                            "AP_START_DATE": datetime.combine(start, datetime.min.time()),
                            "AP_END_DATE": datetime.combine(end, datetime.min.time()),
                            "AP_MONTHLY_PREMIUM": monthly,
                            "AP_CONTRACT_STATE": _status_ap(g, resilie),
                            "AP_BROKER_CODE": f"BRK-{int(g.rng.integers(1, 40)):03d}",
                        }
                    )
                    step, amount = 1, monthly

                # ------------------------------------------------------ paiements
                due = _payment_dates(start, end, step, cfg.extraction_date)
                issues_window = [
                    d
                    for d in due
                    if cfg.snapshot_date - timedelta(days=365) <= d <= cfg.snapshot_date
                ]
                issue_dates = set()
                if issues_window and prof["payment_issues"]:
                    picks = g.rng.choice(
                        len(issues_window),
                        size=min(prof["payment_issues"], len(issues_window)),
                        replace=False,
                    )
                    issue_dates = {issues_window[int(p)] for p in picks}
                for d in due:
                    if src == "AB":
                        if d in issue_dates:  # échéance impayée : aucune ligne côté AB
                            continue
                        pay_seq += 1
                        row = {
                            "AB_PAYMENT_ID": pay_seq,
                            "AB_POLICY_NUMBER": ref,
                            "AB_DATE_PAIEMENT": d + timedelta(days=int(g.rng.integers(0, 5))),
                            "AB_MONTANT": round(amount * step / 12, 2) if step != 12 else amount,
                            "AB_MODE_PAIEMENT": g.choice(AB_MODES),
                        }
                        if g.chance(0.001):
                            row["AB_MONTANT"] = -row["AB_MONTANT"]
                        tables["AB_PAIEMENT"].append(row)
                        if g.chance(0.004):  # doublon technique en source
                            tables["AB_PAIEMENT"].append(dict(row))
                    else:
                        status = (
                            "FAILED"
                            if d in issue_dates
                            else g.choice(["SUCCESS", "SUCCESS", "SUCCESS", "COMPLETED"])
                        )
                        tables["AP_PAYMENTS"].append(
                            {
                                "AP_PAYMENT_REF": str(
                                    uuid.UUID(int=int(g.rng.integers(0, 2**63)))
                                ).upper(),
                                "AP_CONTRACT_REF": ref,
                                "AP_PAYMENT_DATETIME": datetime.combine(d, datetime.min.time())
                                + timedelta(
                                    hours=int(g.rng.integers(0, 24)),
                                    minutes=int(g.rng.integers(0, 60)),
                                ),
                                "AP_AMOUNT_PAID": amount,
                                "AP_PAYMENT_CHANNEL": g.choice(AP_CANAUX),
                                "AP_TRANSACTION_STATUS": status,
                            }
                        )

                # ------------------------------------------------------ sinistres
                n_claims = (prof["claims_12m"] if k == 0 else 0) + int(g.rng.poisson(0.15))
                for c in range(n_claims):
                    in_window = c < prof["claims_12m"] and k == 0
                    when = (
                        g.rand_dt(cfg.snapshot_date - timedelta(days=360), cfg.snapshot_date)
                        if in_window
                        else g.rand_dt(date(2023, 1, 1), cfg.extraction_date)
                    )
                    refused = in_window and prof["claim_refused"] and c == 0
                    claim_seq[src] += 1
                    montant = round(float(g.rng.lognormal(7, 1)), 2)
                    texte = g.choice(
                        [
                            "Dégât des eaux cuisine",
                            "Bris de glace pare-brise",
                            "Accrochage parking supermarché",
                            "Vol de véhicule",
                            "Sinistre déclaré par téléphone, rappeler au 06 12 34 56 78",
                            "Client joignable sur client.test@gmail.com",
                        ]
                    )
                    if src == "AB":
                        tables["AB_SINISTRE"].append(
                            {
                                "AB_CLAIM_ID": claim_seq[src],
                                "AB_POLICY_NUMBER": ref,
                                "AB_DATE_SINISTRE": when,
                                "AB_MONTANT_ESTIME": montant,
                                "AB_STATUT_SINISTRE": "REFUSE"
                                if refused
                                else g.choice(["CLOS", "INDEMNISE", "EN_COURS", "OUVERT"]),
                                "AB_DESCRIPTION": texte,
                            }
                        )
                    else:
                        txt = when.strftime("%Y-%m-%d %H:%M:%S")
                        if g.chance(0.01):
                            txt = txt.replace("-0", "-3")  # date impossible (ex. 2026-36-..)
                        tables["AP_CLAIMS"].append(
                            {
                                "AP_SINISTRE_NUM": claim_seq[src],
                                "AP_CONTRACT_REF": ref,
                                "AP_INCIDENT_DATE": txt,
                                "AP_ESTIMATED_AMOUNT": montant,
                                "AP_CLAIM_STATUS": "REJECTED"
                                if refused
                                else g.choice(["CLOSED", "PAID", "IN_PROGRESS", "OPEN"]),
                                "AP_CLAIM_COMMENT": texte,
                                "AP_FRAUD_SCORE": round(float(g.rng.beta(2, 8) * 100), 2),
                            }
                        )
    return {name: pd.DataFrame(rows) for name, rows in tables.items()}


def write_sqlite(tables: dict[str, pd.DataFrame], ab_path: Path, ap_path: Path) -> dict[str, Path]:
    """Écrit les tables AB_* dans la base « Oracle » et AP_* dans la base « SQL Server »."""
    paths = {"AB": Path(ab_path), "AP": Path(ap_path)}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
    for name, df in tables.items():
        with sqlite3.connect(paths[name[:2]]) as conn:
            df.to_sql(name, conn, index=False)
    return paths
