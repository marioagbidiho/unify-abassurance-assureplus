"""Application web UNIFY : pilotage du risque de résiliation (processus P9 + client de P8).

Lancement local : ``streamlit run app/streamlit_app.py``
Comptes de démonstration : admin / Admin#2026!   ·   user / User#2026!
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unify.api.security import authenticate  # noqa: E402
from unify.ml.features import FEATURE_LABELS, FEATURES  # noqa: E402
from unify.ml.predict import Predictor, log_prediction  # noqa: E402

ARTIFACTS = Path(os.environ.get("UNIFY_ARTIFACTS", ROOT / "data" / "demo"))
COLORS = {"ÉLEVÉ": "#c0392b", "MOYEN": "#e67e22", "FAIBLE": "#27ae60"}

st.set_page_config(page_title="UNIFY · Risque de résiliation", page_icon="📊", layout="wide")


# ------------------------------------------------------------------ données
@st.cache_resource
def load_predictor() -> Predictor:
    return Predictor.load(ARTIFACTS)


@st.cache_data
def load_scores() -> pd.DataFrame:
    return pd.read_parquet(ARTIFACTS / "scores.parquet")


@st.cache_data
def load_report() -> dict:
    return json.loads((ARTIFACTS / "pipeline_report.json").read_text(encoding="utf-8"))


def fmt(n: float, decimals: int = 0) -> str:
    """Format français : espace pour les milliers, virgule décimale."""
    return f"{n:,.{decimals}f}".replace(",", " ").replace(".", ",")


# ------------------------------------------------------------------ authentification
def login_view() -> None:
    st.title("UNIFY · Plateforme Data & IA AbAssurance / AssurePlus")
    st.caption("Prédiction de la résiliation à 90 jours - Bloc 3 BCD&IA - AGBIDIHO Mario")
    with st.form("login"):
        username = st.text_input("Identifiant")
        password = st.text_input("Mot de passe", type="password")
        if st.form_submit_button("Se connecter", type="primary"):
            user = authenticate(username, password)
            if user is None:
                st.error("Identifiants incorrects.")
            else:
                st.session_state["user"] = {"username": user.username, "role": user.role}
                st.rerun()
    st.info("Comptes de démonstration : **admin** / `Admin#2026!` · **user** / `User#2026!`")


# ------------------------------------------------------------------ vues métier
def portfolio_view(scores: pd.DataFrame) -> None:
    st.subheader("Portefeuille : clients à risque de résiliation")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Clients observés", fmt(len(scores)))
    c2.metric("Risque élevé", fmt((scores["niveau_risque"] == "ÉLEVÉ").sum()))
    c3.metric("Score moyen", fmt(scores["score"].mean() * 100, 1) + " %")
    c4.metric(
        "Prime annuelle exposée (risque élevé)",
        fmt(scores.loc[scores["niveau_risque"] == "ÉLEVÉ", "prime_annuelle_totale"].sum()) + " €",
    )
    f1, f2, f3 = st.columns(3)
    source = f1.multiselect("Compagnie d'origine", ["AB", "AP"], default=["AB", "AP"])
    niveaux = f2.multiselect("Niveau de risque", list(COLORS), default=["ÉLEVÉ", "MOYEN"])
    n = f3.slider("Nombre de clients", 10, 500, 50, step=10)
    view = scores[scores["source_systeme"].isin(source) & scores["niveau_risque"].isin(niveaux)]
    st.dataframe(
        view.head(n)[
            [
                "id_client_uni",
                "source_systeme",
                "score",
                "niveau_risque",
                "age",
                "anciennete_mois",
                "nb_contrats_actifs",
                "prime_annuelle_totale",
                "nb_paiements_rejetes_12m",
                "nb_sinistres_refuses_12m",
            ]
        ],
        width="stretch",
        hide_index=True,
        column_config={"score": st.column_config.ProgressColumn("Score", min_value=0, max_value=1)},
    )
    chart = (
        alt.Chart(scores)
        .mark_bar()
        .encode(
            x=alt.X("score:Q", bin=alt.Bin(maxbins=30), title="Score de résiliation"),
            y=alt.Y("count():Q", title="Nombre de clients"),
            color=alt.Color("source_systeme:N", title="Source"),
        )
        .properties(height=220)
    )
    st.altair_chart(chart, width="stretch")


def client_view(scores: pd.DataFrame, predictor: Predictor, username: str) -> None:
    st.subheader("Fiche client : score expliqué")
    default = scores["id_client_uni"].iloc[0]
    client_id = (
        st.selectbox(
            "Identifiant client unifié (pseudonyme)", scores["id_client_uni"].head(300), index=0
        )
        or default
    )
    row = scores.set_index("id_client_uni").loc[client_id]
    result = predictor.predict_one(row)
    log_prediction(ARTIFACTS / "journal_predictions.jsonl", client_id, result, username)
    c1, c2, c3 = st.columns(3)
    c1.metric("Score", fmt(result["score"] * 100, 1) + " %")
    c2.markdown(
        f"### <span style='color:{COLORS[result['niveau_risque']]}'>"
        f"Risque {result['niveau_risque']}</span>",
        unsafe_allow_html=True,
    )
    c3.metric("Résilié dans les 90 j (réalité)", "Oui" if row["cible_resiliation_90j"] else "Non")
    st.markdown("**Principaux facteurs qui augmentent le risque**")
    if result["facteurs"]:
        factors = pd.DataFrame(result["facteurs"])
        st.altair_chart(
            alt.Chart(factors)
            .mark_bar(color="#c0392b")
            .encode(
                x=alt.X("contribution:Q", title="Contribution au score"),
                y=alt.Y("libelle:N", sort="-x", title=None),
                tooltip=["libelle", "valeur", "reference", "contribution"],
            )
            .properties(height=150),
            width="stretch",
        )
        st.dataframe(
            factors[["libelle", "valeur", "reference", "contribution"]],
            hide_index=True,
            width="stretch",
        )
    else:
        st.success("Aucun facteur n'augmente le risque par rapport au client de référence.")
    with st.expander("Toutes les caractéristiques du client"):
        st.dataframe(
            pd.DataFrame(
                {
                    "Caractéristique": [FEATURE_LABELS[f] for f in FEATURES],
                    "Valeur": [row[f] for f in FEATURES],
                }
            ),
            hide_index=True,
            width="stretch",
        )
    st.caption(
        "Score d'aide à la décision : toute action reste validée par un conseiller "
        "(RGPD art. 22). Chaque consultation est journalisée."
    )


def simulator_view(predictor: Predictor) -> None:
    st.subheader("Simulateur : scorer un profil")
    with st.form("simu"):
        c1, c2, c3 = st.columns(3)
        values = {
            "source_ap": int(c1.selectbox("Compagnie", ["AB", "AP"]) == "AP"),
            "age": c1.number_input("Âge", 18, 100, 35),
            "anciennete_mois": c1.number_input("Ancienneté (mois)", 0, 600, 24),
            "nb_contrats_actifs": c1.number_input("Contrats actifs", 1, 10, 1),
            "nb_produits_distincts": c2.number_input("Produits différents", 1, 5, 1),
            "prime_annuelle_totale": c2.number_input(
                "Prime annuelle totale (€)", 50.0, 20000.0, 700.0
            ),
            "a_produit_auto": int(c2.checkbox("Contrat auto", True)),
            "a_produit_habitation": int(c2.checkbox("Contrat habitation")),
            "a_produit_sante": int(c2.checkbox("Contrat santé")),
            "nb_sinistres_12m": c3.number_input("Sinistres 12 mois", 0, 20, 0),
            "montant_sinistres_12m": c3.number_input(
                "Montant sinistres 12 mois (€)", 0.0, 1e6, 0.0
            ),
            "nb_sinistres_refuses_12m": c3.number_input("Sinistres refusés 12 mois", 0, 20, 0),
            "nb_paiements_12m": c3.number_input("Paiements 12 mois", 0, 60, 12),
            "nb_paiements_rejetes_12m": c3.number_input("Paiements rejetés 12 mois", 0, 60, 0),
            "taux_paiement_12m": c1.number_input("Taux de paiement 12 mois", 0.0, 3.0, 1.0),
            "jours_depuis_dernier_paiement": c2.number_input(
                "Jours depuis dernier paiement", 0, 999, 20
            ),
            "nb_comptes_lies": c3.number_input("Comptes liés (2 compagnies)", 1, 3, 1),
        }
        loyalty = c1.number_input("Score de fidélité (AP, -1 = non renseigné)", -1, 100, -1)
        submitted = st.form_submit_button("Calculer le score", type="primary")
    if submitted:
        values["score_fidelite"] = None if loyalty < 0 else loyalty
        values["fidelite_renseignee"] = int(loyalty >= 0)
        result = predictor.predict_one(pd.Series(values, dtype="float64"))
        st.metric(
            "Score de résiliation",
            fmt(result["score"] * 100, 1) + " %",
            help=f"Niveau : {result['niveau_risque']}",
        )
        st.json(result["facteurs"])


# ------------------------------------------------------------------ vues admin
def quality_view(report: dict) -> None:
    st.subheader("Qualité des données (processus P4)")
    rows = [
        {
            "Entité": e,
            "Lues": q["nb_lignes_lues"],
            "Doublons supprimés": q["nb_doublons_supprimes"],
            "Valides (SILVER)": q["nb_lignes_valides"],
            "Quarantaine": q["nb_lignes_quarantaine"],
            "Taux de conformité": f"{q['taux_conformite'] * 100:.2f} %",
        }
        for e, q in report["qualite"].items()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    rules = pd.DataFrame(
        [
            {
                "Entité": e,
                "Règle": r,
                "Sévérité": v["severite"],
                "Lignes": v["nb_lignes"],
                "Description": v["description"],
            }
            for e, q in report["qualite"].items()
            for r, v in q["regles"].items()
        ]
    )
    st.altair_chart(
        alt.Chart(rules[rules["Lignes"] > 0])
        .mark_bar()
        .encode(
            x=alt.X("Lignes:Q"),
            y=alt.Y("Règle:N", sort="-x"),
            color="Sévérité:N",
            row=alt.Row("Entité:N"),
            tooltip=["Description", "Lignes"],
        )
        .properties(height=90),
        width="content",
    )
    st.dataframe(rules, hide_index=True, width="stretch")
    st.subheader("Consolidation client (MDM, processus P5)")
    mdm = report["mdm"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Comptes clients SILVER", fmt(mdm["nb_clients"]))
    c2.metric("Personnes uniques", fmt(mdm["nb_personnes"]))
    c3.metric("Clients communs AB/AP", fmt(mdm["nb_doublons_inter_societes"]))
    st.dataframe(pd.DataFrame(mdm["par_regle"]).T, width="stretch")


def pipeline_view(report: dict) -> None:
    st.subheader("Lignage du pipeline : sources → Kafka → BRONZE → SILVER → GOLD")
    sources = report["sources"]
    lineage = pd.DataFrame(
        [
            {"Étape": "1. Sources (Oracle + SQL Server)", "Lignes": sum(sources.values())},
            {"Étape": "2. Événements Kafka", "Lignes": sum(report["kafka"].values())},
            {"Étape": "3. BRONZE (brut)", "Lignes": sum(report["bronze"].values())},
            {
                "Étape": "4. SILVER (conformes)",
                "Lignes": sum(q["nb_lignes_valides"] for q in report["qualite"].values()),
            },
            {
                "Étape": "5. GOLD (clients observés)",
                "Lignes": report["gold"]["nb_clients_observes"],
            },
        ]
    )
    st.dataframe(lineage, hide_index=True, width="stretch")
    st.json({"sources": sources, "topics_kafka": report["kafka"]})
    st.caption(
        f"Exécution du {report['date_execution_utc']} UTC - durée {report['duree_secondes']} s"
    )


def model_view(card: dict) -> None:
    st.subheader(f"Modèle champion : {card['nom']}")
    m = card["metriques_test"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("AUC (test)", fmt(m["auc"], 3), help="Seuil d'acceptation : 0,75")
    c2.metric("Lift 1er décile", fmt(m["lift_decile_1"], 2), help="Seuil d'acceptation : 3")
    c3.metric("Précision top 10 %", fmt(m["precision_top10"] * 100, 1) + " %")
    c4.metric("Statut", "ACCEPTÉ" if card["accepte"] else "REFUSÉ")
    st.markdown("**Comparaison des algorithmes (même jeu de test)**")
    st.dataframe(pd.DataFrame(card["comparaison"]).T, width="stretch")
    roc = pd.DataFrame(card["courbe_roc"])
    st.altair_chart(
        alt.Chart(roc)
        .mark_line()
        .encode(
            x=alt.X("fpr:Q", title="Taux de faux positifs"),
            y=alt.Y("tpr:Q", title="Taux de vrais positifs"),
        )
        .properties(height=260, title="Courbe ROC"),
        width="stretch",
    )
    st.markdown("**Contrôle d'équité (ENF-10)**")
    c1, c2 = st.columns(2)
    c1.dataframe(pd.DataFrame(card["equite"]["par_source"]), hide_index=True)
    c2.dataframe(pd.DataFrame(card["equite"]["par_tranche_age"]), hide_index=True)
    with st.expander("Limites connues (model card)"):
        for line in card["limites"]:
            st.write("- " + line)


def journal_view() -> None:
    st.subheader("Journal des prédictions (D7 - audit AI Act)")
    journal = ARTIFACTS / "journal_predictions.jsonl"
    if not journal.exists():
        st.info("Aucune prédiction journalisée pour l'instant.")
        return
    lines = journal.read_text(encoding="utf-8").strip().splitlines()[-200:]
    df = pd.DataFrame([json.loads(x) for x in lines])
    st.dataframe(
        df[
            [
                "horodatage_utc",
                "utilisateur",
                "id_client_uni",
                "score",
                "niveau_risque",
                "version_modele",
            ]
        ].iloc[::-1],
        hide_index=True,
        width="stretch",
    )


# ------------------------------------------------------------------ routeur
def main() -> None:
    if "user" not in st.session_state:
        login_view()
        return
    user = st.session_state["user"]
    with st.sidebar:
        st.markdown(f"**Connecté :** {user['username']}  \n**Rôle :** {user['role']}")
        if st.button("Se déconnecter"):
            del st.session_state["user"]
            st.rerun()
        st.divider()
        st.caption("UNIFY · AbAssurance × AssurePlus\n\nBloc 3 BCD&IA · AGBIDIHO Mario")

    predictor, scores = load_predictor(), load_scores()
    st.title("UNIFY · Risque de résiliation à 90 jours")
    tabs = ["Portefeuille", "Fiche client", "Simulateur"]
    if user["role"] == "admin":
        tabs += ["Qualité & MDM", "Pipeline", "Modèle", "Journal"]
    views = st.tabs(tabs)
    with views[0]:
        portfolio_view(scores)
    with views[1]:
        client_view(scores, predictor, user["username"])
    with views[2]:
        simulator_view(predictor)
    if user["role"] == "admin":
        report = load_report()
        with views[3]:
            quality_view(report)
        with views[4]:
            pipeline_view(report)
        with views[5]:
            model_view(predictor.card)
        with views[6]:
            journal_view()


main()
