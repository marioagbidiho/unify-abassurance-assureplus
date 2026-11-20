"""Chargement des référentiels unifiés (config/referentiels.yaml) et traduction des codes."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from unify.common.text import normalize_code
from unify.config import PROJECT_ROOT

REF_FILE = PROJECT_ROOT / "config" / "referentiels.yaml"


@lru_cache(maxsize=4)
def load_referentials(path: Path = REF_FILE) -> dict[str, dict]:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@lru_cache(maxsize=32)
def _lookup(ref_name: str, source: str) -> dict[str, str]:
    """Dictionnaire {code source normalisé -> code unifié} pour une source (ab / ap)."""
    refs = load_referentials()
    if ref_name not in refs:
        raise KeyError(f"Référentiel inconnu : {ref_name}")
    table: dict[str, str] = {}
    for code_uni, spec in refs[ref_name].items():
        for code_source in spec.get(source.lower(), []) or []:
            table[normalize_code(str(code_source))] = code_uni
    return table


def translate(ref_name: str, source: str, value: object) -> str | None:
    """Traduit un code source ; None si absent ou non référencé (règle qualité REF_INCONNU)."""
    code = normalize_code(value)
    if code is None:
        return None
    return _lookup(ref_name, source).get(code)


def referential_frame(ref_name: str) -> pd.DataFrame:
    """Table ref_* au format du modèle canonique (code_uni, libelle, code_source_ab/ap, domaine)."""
    rows = []
    for code_uni, spec in load_referentials()[ref_name].items():
        rows.append(
            {
                "code_uni": code_uni,
                "libelle": spec["libelle"],
                "code_source_ab": "|".join(map(str, spec.get("ab") or [])),
                "code_source_ap": "|".join(map(str, spec.get("ap") or [])),
                "domaine": ref_name.removeprefix("ref_"),
            }
        )
    return pd.DataFrame(rows)
