"""Organisation physique du data lake (nomenclature du Bloc 1).

    /data/bronze/{source}/{entité}/date_ingestion=AAAA-MM-JJ/   brut, immuable
    /data/silver/uni_{entité}/                                  modèle canonique
    /data/quarantaine/{entité}/date_ingestion=AAAA-MM-JJ/       rejets qualité + motif
    /data/gold/ft_* et dm_*                                     features et datamarts

``root`` vaut un chemin local en DEV/CI et ``hdfs://namenode:8020`` en PRD (les jobs Spark
utilisent les mêmes chemins relatifs). Format : Parquet (Iceberg en PRD).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Lake:
    root: str

    def path(self, *parts: str) -> str:
        return "/".join([self.root.rstrip("/"), "data", *parts])

    def bronze(self, source: str, entity: str, day: date | None = None) -> str:
        base = self.path("bronze", source.lower(), entity)
        return base if day is None else f"{base}/date_ingestion={day.isoformat()}"

    def silver(self, entity: str) -> str:
        return self.path("silver", f"uni_{entity}")

    def mdm(self) -> str:
        return self.path("silver", "mdm_personne")

    def quarantine(self, entity: str, day: date) -> str:
        return self.path("quarantaine", entity, f"date_ingestion={day.isoformat()}")

    def gold(self, table: str) -> str:
        return self.path("gold", table)

    # ------------------------------------------------------------------ pandas I/O
    @staticmethod
    def write(df: pd.DataFrame, location: str, name: str = "part-00000.parquet") -> str:
        target = Path(location)
        target.mkdir(parents=True, exist_ok=True)
        out = target / name
        df.to_parquet(out, index=False)
        return str(out)

    @staticmethod
    def read(location: str) -> pd.DataFrame:
        files = sorted(Path(location).rglob("*.parquet"))
        if not files:
            return pd.DataFrame()
        return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
