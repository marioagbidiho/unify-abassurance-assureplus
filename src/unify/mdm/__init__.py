"""Consolidation client (processus P5) : rapprochement AB / AP et golden record."""

from unify.mdm.matching import build_golden_records, jaro_winkler

__all__ = ["build_golden_records", "jaro_winkler"]
