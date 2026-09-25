"""Horse racing — first-class sport with **no odds provider wired in yet**.

Horse racing is part of Kai's approved scope (``scope.APPROVED_SPORTS``) and is
registered as a sport/market in the schema, but none of the configured providers
(``odds_api_io``, ``sportsgameodds``, the legacy ``odds_api``) publish racing
odds. Rather than fabricate fixtures or silently return nothing, this adapter
exists so the gap is explicit and machine-readable:

    HorseRacingSource().provider_status == {
        "connected": False,
        "status": "no_source",
        "sport": "horse_racing",
        "detail": "...",
        "integration": "docs/KAIBET_HORSE_RACING.md",
    }

Plugging in a real feed later
-----------------------------
1. Implement ``fetch_events`` / ``fetch_odds`` here against the chosen feed
   (e.g. Timeform, Racing Post, Betfair Exchange Historical/Betting API,
   The Racing API). Keep the Odds-API.io-shaped event dict so
   ``DataIngestionManager._ingest_event_v3`` can consume it unchanged:
   ``{id, home, away, date, status, league: {name, slug}, sport: {name, slug}}``
   where, for racing, ``home`` is the meeting/race and ``away`` the race time
   or distance marker.
2. Add a slug to ``scope._HORSE_RACING`` / ``SPORT_SLUG_MAP`` so
   ``_sports_to_slugs()`` returns a value, and remove ``horse_racing`` from the
   ``SPORTS_WITH_SOURCE`` exclusion in ``scope.py``.
3. Add the provider to ``DATA_SOURCES`` in ``data_sources/__init__.py`` and let
   ``source_status()`` report it as ``available``.
4. Extend the market normalisation in ``odds_api_io.MARKET_NORMALIZATION`` for
   win/place/each-way (``match_result`` already covers Win) and mark the
   racing families modelled in ``markets.py``.

Until then this adapter deliberately reports ``no_source`` and returns no data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DETAIL = (
    "Horse racing is approved at the scope layer but no odds provider is "
    "configured for it. No events or odds are fetched, and nothing is "
    "fabricated. See docs/KAIBET_HORSE_RACING.md for the integration path."
)


class HorseRacingSource:
    """Placeholder adapter that reports horse racing has no data source."""

    name = "horse_racing"

    @property
    def is_configured(self) -> bool:
        return False

    @property
    def provider_status(self) -> Dict[str, Any]:
        return {
            "connected": False,
            "status": "no_source",
            "sport": "horse_racing",
            "detail": _DETAIL,
            "integration": "docs/KAIBET_HORSE_RACING.md",
        }

    def fetch_sports(self) -> list:
        return []

    def fetch_events(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def fetch_odds(self, *args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return None

    def health_check(self) -> Dict[str, Any]:
        status = self.provider_status
        return {"ok": False, "status": status["status"], "detail": status["detail"]}
