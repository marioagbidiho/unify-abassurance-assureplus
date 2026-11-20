"""Rapprochement des clients communs aux deux compagnies (Master Data Management).

Aucune clé commune n'existe entre AB_CLIENT et AP_USERS. Trois règles sont appliquées,
de la plus sûre à la moins sûre ; chaque lien porte la règle et un score de confiance :

* R1_EMAIL_DDN   : même e-mail pseudonymisé ET même date de naissance        (score 1,00)
* R2_IDENTITE    : même nom + prénom normalisés ET même date de naissance     (score 0,97)
* R3_PROBABILISTE: même date de naissance et code postal, similarité
                   Jaro-Winkler des noms complets >= seuil                     (score = similarité)

Les liens sont regroupés par composantes connexes (union-find) : un groupe = une personne
(``mdm_personne``), identifiée par un UUID déterministe.
"""

from __future__ import annotations

import uuid

import pandas as pd

from unify.common.text import match_key

MDM_NAMESPACE = uuid.UUID("6f1c2a8e-3b0d-4c55-9a51-0e7f4b2d9c11")
DEFAULT_THRESHOLD = 0.92
RULE_SCORES = {"R1_EMAIL_DDN": 1.0, "R2_IDENTITE": 0.97}
RULE_PRIORITY = {"R1_EMAIL_DDN": 1, "R2_IDENTITE": 2, "R3_PROBABILISTE": 3}


def jaro_winkler(a: str | None, b: str | None, prefix_scale: float = 0.1) -> float:
    """Similarité de Jaro-Winkler entre deux chaînes (0 = différentes, 1 = identiques)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    a_flags, b_flags = [False] * len(a), [False] * len(b)
    matches = 0
    for i, char in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_flags[j] and b[j] == char:
                a_flags[i] = b_flags[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    a_matched = [c for c, f in zip(a, a_flags, strict=True) if f]
    b_matched = [c for c, f in zip(b, b_flags, strict=True) if f]
    transpositions = sum(x != y for x, y in zip(a_matched, b_matched, strict=True)) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[max(root_a, root_b)] = min(root_a, root_b)


def _prepare(clients: pd.DataFrame) -> pd.DataFrame:
    df = clients[
        [
            "id_client_uni",
            "source_systeme",
            "nom",
            "prenom",
            "date_naissance",
            "email_hash",
            "code_postal",
        ]
    ].copy()
    df["nom_cle"] = df["nom"].map(match_key)
    df["prenom_cle"] = df["prenom"].map(match_key)
    df["nom_complet_cle"] = (
        df["prenom_cle"].fillna("") + " " + df["nom_cle"].fillna("")
    ).str.strip()
    df["ddn"] = df["date_naissance"].astype(str)
    return df


def _pairs(left: pd.DataFrame, right: pd.DataFrame, on: list[str]) -> pd.DataFrame:
    merged = left.merge(right, on=on, suffixes=("_ab", "_ap"))
    return merged[
        ["id_client_uni_ab", "id_client_uni_ap", "nom_complet_cle_ab", "nom_complet_cle_ap"]
    ]


def find_links(clients: pd.DataFrame, threshold: float = DEFAULT_THRESHOLD) -> pd.DataFrame:
    """Retourne les liens (id_ab, id_ap, regle, score) entre clients AB et AP."""
    df = _prepare(clients)
    ab = df[df["source_systeme"] == "AB"]
    ap = df[df["source_systeme"] == "AP"]
    links = []

    r1 = _pairs(
        ab.dropna(subset=["email_hash", "date_naissance"]),
        ap.dropna(subset=["email_hash", "date_naissance"]),
        ["email_hash", "ddn"],
    )
    links.append(r1.assign(regle="R1_EMAIL_DDN", score=RULE_SCORES["R1_EMAIL_DDN"]))

    r2 = _pairs(
        ab.dropna(subset=["nom_cle", "prenom_cle", "date_naissance"]),
        ap.dropna(subset=["nom_cle", "prenom_cle", "date_naissance"]),
        ["nom_cle", "prenom_cle", "ddn"],
    )
    links.append(r2.assign(regle="R2_IDENTITE", score=RULE_SCORES["R2_IDENTITE"]))

    r3 = _pairs(
        ab.dropna(subset=["date_naissance", "code_postal"]),
        ap.dropna(subset=["date_naissance", "code_postal"]),
        ["ddn", "code_postal"],
    )
    if not r3.empty:
        r3 = r3.assign(
            score=[
                jaro_winkler(a, b)
                for a, b in zip(r3["nom_complet_cle_ab"], r3["nom_complet_cle_ap"], strict=True)
            ]
        )
        r3 = r3[r3["score"] >= threshold].assign(regle="R3_PROBABILISTE")
    links.append(r3)

    all_links = pd.concat(
        [lk for lk in links if not lk.empty]
        or [pd.DataFrame(columns=["id_client_uni_ab", "id_client_uni_ap", "regle", "score"])],
        ignore_index=True,
    )
    # Pour chaque paire, on garde la règle la plus sûre (priorité R1 > R2 > R3) : une
    # similarité probabiliste de 1,0 ne doit pas masquer une règle déterministe.
    all_links = (
        all_links.assign(_prio=all_links["regle"].map(RULE_PRIORITY))
        .sort_values(["_prio", "score"], ascending=[True, False])
        .drop_duplicates(subset=["id_client_uni_ab", "id_client_uni_ap"])
    )
    return all_links[["id_client_uni_ab", "id_client_uni_ap", "regle", "score"]].reset_index(
        drop=True
    )


def build_golden_records(
    clients: pd.DataFrame, threshold: float = DEFAULT_THRESHOLD
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Construit ``mdm_personne`` et renvoie les clients enrichis de ``id_personne_mdm``."""
    links = find_links(clients, threshold)
    uf = _UnionFind(clients["id_client_uni"].tolist())
    for row in links.itertuples(index=False):
        uf.union(row.id_client_uni_ab, row.id_client_uni_ap)

    roots = clients["id_client_uni"].map(uf.find)
    person_ids = roots.map(lambda root: str(uuid.uuid5(MDM_NAMESPACE, root)))
    enriched = clients.assign(id_personne_mdm=person_ids.values)

    link_info = pd.concat(
        [
            links[["id_client_uni_ab", "regle", "score"]].rename(
                columns={"id_client_uni_ab": "id"}
            ),
            links[["id_client_uni_ap", "regle", "score"]].rename(
                columns={"id_client_uni_ap": "id"}
            ),
        ]
    )
    link_info["id_personne_mdm"] = link_info["id"].map(
        dict(zip(enriched["id_client_uni"], enriched["id_personne_mdm"], strict=True))
    )

    personnes = enriched.groupby("id_personne_mdm").agg(nb_comptes_lies=("id_client_uni", "size"))
    best = (
        link_info.sort_values("score")
        .groupby("id_personne_mdm")
        .agg(regle_rapprochement=("regle", "first"), score_confiance=("score", "min"))
    )
    personnes = personnes.join(best, how="left").reset_index()
    personnes["regle_rapprochement"] = personnes["regle_rapprochement"].fillna("UNIQUE")
    personnes["score_confiance"] = personnes["score_confiance"].fillna(1.0).round(2)
    return personnes[
        ["id_personne_mdm", "regle_rapprochement", "score_confiance", "nb_comptes_lies"]
    ], enriched
