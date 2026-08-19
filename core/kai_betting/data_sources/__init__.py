"""Kai Betting — Data Source Providers.

Each provider implements a standard fetch interface for sports data.
Providers register themselves via a module-level dict so the ingestion
manager can discover active sources at runtime.
"""

from core.kai_betting.data_sources.odds_api_io import OddsAPIioSource
from core.kai_betting.data_sources.odds_api import OddsAPISource  # legacy
from core.kai_betting.data_sources.sportsgameodds import SportsGameOddsSource  # supplemental

__all__ = ["OddsAPIioSource", "OddsAPISource", "SportsGameOddsSource"]

# Registry of available data sources (keyed by provider name)
# odds_api_io is the PRIMARY provider. odds_api is the legacy fallback
# (also the sole source of settlement scores). sportsgameodds is a
# supplemental, independently-gated source (own quota, own sync interval).
DATA_SOURCES = {
    "odds_api_io": OddsAPIioSource,
    "odds_api": OddsAPISource,
    "sportsgameodds": SportsGameOddsSource,
}
