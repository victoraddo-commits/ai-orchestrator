"""Kai Betting — Data Source Providers.

Each provider implements a standard fetch interface for sports data:
  - fetch_events(sport_keys, days_ahead) → upcoming matches/games
  - fetch_odds(sport_keys) → current bookmaker odds
  - fetch_results(sport_keys) → completed match scores

Providers register themselves via a module-level dict so the ingestion
manager can discover active sources at runtime.
"""

from core.kai_betting.data_sources.odds_api import OddsAPISource

__all__ = ["OddsAPISource"]

# Registry of available data sources (keyed by provider name)
DATA_SOURCES = {
    "odds_api": OddsAPISource,
}
