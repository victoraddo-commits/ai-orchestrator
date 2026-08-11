"""The Odds API — sports data provider.

Covers 8 of 10 Kai sports with events, odds, and scores via
the free tier (500 requests/month).

Endpoint summary:
  GET /v4/sports                           — free, list in-season sports
  GET /v4/sports/{sport}/events            — free, upcoming events
  GET /v4/sports/{sport}/odds              — 1 credit / market / region
  GET /v4/sports/{sport}/scores            — 1 credit

Response headers track usage:
  x-requests-remaining, x-requests-used, x-requests-last
"""

from __future__ import annotations

import os
import re
import time
import json
import logging
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

# ── Sport Key Mapping ──────────────────────────────────────────────────────
# The Odds API returns sport keys like "soccer_epl", "basketball_nba".
# Each maps to one Kai internal sport key.  Keys NOT in this map are
# silently skipped during ingestion.

SPORT_KEY_MAP: Dict[str, str] = {
    # ── Football (soccer) ──
    "soccer_epl": "football",
    "soccer_efl_champ": "football",
    "soccer_spain_la_liga": "football",
    "soccer_spain_segunda_division": "football",
    "soccer_italy_serie_a": "football",
    "soccer_italy_serie_b": "football",
    "soccer_germany_bundesliga": "football",
    "soccer_germany_bundesliga2": "football",
    "soccer_france_ligue_one": "football",
    "soccer_france_ligue_two": "football",
    "soccer_netherlands_eredivisie": "football",
    "soccer_portugal_primeira_liga": "football",
    "soccer_brazil_campeonato": "football",
    "soccer_brazil_serie_b": "football",
    "soccer_argentina_primera_division": "football",
    "soccer_turkey_super_lig": "football",
    "soccer_belgium_first_div": "football",
    "soccer_russia_premier_league": "football",
    "soccer_japan_j_league": "football",
    "soccer_korea_kleague1": "football",
    "soccer_china_superleague": "football",
    "soccer_uefa_champs_league": "football",
    "soccer_uefa_europa_league": "football",
    "soccer_uefa_europa_conference_league": "football",
    "soccer_uefa_nations_league": "football",
    "soccer_usa_mls": "football",
    "soccer_australia_aleague": "football",
    "soccer_mexico_ligamx": "football",
    "soccer_egypt_premier_league": "football",
    "soccer_south_africa_premier_league": "football",
    "soccer_fifa_world_cup": "football",
    "soccer_fifa_world_cup_womens": "football",
    "soccer_uefa_euro": "football",
    "soccer_copa_america": "football",
    # ── Basketball ──
    "basketball_nba": "basketball",
    "basketball_ncaab": "basketball",
    "basketball_wnba": "basketball",
    "basketball_euroleague": "basketball",
    "basketball_nbl": "basketball",
    "basketball_spain_acb": "basketball",
    # ── Tennis ──
    "tennis_atp": "tennis",
    "tennis_wta": "tennis",
    "tennis_atp_challenger": "tennis",
    "tennis_itf_men": "tennis",
    "tennis_itf_women": "tennis",
    # ── Baseball ──
    "baseball_mlb": "baseball",
    "baseball_npb": "baseball",
    "baseball_kbo": "baseball",
    # ── Ice Hockey ──
    "icehockey_nhl": "ice_hockey",
    "icehockey_sweden_hockey_league": "ice_hockey",
    "icehockey_sweden_hockey_allsvenskan": "ice_hockey",
    # ── American Football ──
    "americanfootball_nfl": "american_football",
    "americanfootball_ncaaf": "american_football",
    "americanfootball_cfl": "american_football",
    # ── Rugby ──
    "rugbyleague_nrl": "rugby",
    "rugbyunion_world_cup": "rugby",
    "rugbyunion_six_nations": "rugby",
    "rugbyunion_premiership": "rugby",
    # ── Cricket ──
    "cricket_ipl": "cricket",
    "cricket_odi": "cricket",
    "cricket_t20": "cricket",
    "cricket_test_match": "cricket",
    "cricket_big_bash": "cricket",
}

# Reverse map: Kai sport → list of Odds API sport keys
_KAI_TO_ODDS: Dict[str, List[str]] = {}
for _k, _v in SPORT_KEY_MAP.items():
    _KAI_TO_ODDS.setdefault(_v, []).append(_k)


def odds_sport_keys_for(kai_sport: str) -> List[str]:
    """Return The Odds API sport keys that map to a given Kai sport."""
    return _KAI_TO_ODDS.get(kai_sport, [])


def kai_sport_for(odds_sport_key: str) -> Optional[str]:
    """Return the Kai sport key for a The Odds API sport key."""
    return SPORT_KEY_MAP.get(odds_sport_key)


# ── Market Mapping ─────────────────────────────────────────────────────────

# The Odds API market keys → (kai_market_type, list of selection names)
MARKET_MAP = {
    "h2h": "match_result",
    "totals": "over_under",
    "spreads": "handicap",
}

# Markets we request from the API (comma-delimited query param)
_DEFAULT_MARKETS = "h2h,totals"


def _selection_from_outcome_name(name: str, market_type: str,
                                  home: str, away: str) -> str:
    """Map an outcome name to a Kai selection string.

    For h2h (match_result): "Home Team" → "home", "Draw" → "draw", "Away Team" → "away"
    For totals (over_under): "Over" → "over", "Under" → "under"
    """
    name_lower = name.strip().lower()
    if market_type == "match_result":
        if name_lower == home.lower():
            return "home"
        if name_lower == away.lower():
            return "away"
        if name_lower in ("draw", "tie"):
            return "draw"
        return name_lower
    if market_type == "over_under":
        if "over" in name_lower:
            return "over"
        if "under" in name_lower:
            return "under"
        return name_lower
    return name_lower


# ── Odds API Client ────────────────────────────────────────────────────────

class OddsAPISource:
    """Client for The Odds API v4.

    Reads API key from the ODDS_API_KEY environment variable.
    Tracks monthly usage credits in the data_sources DB table.
    """

    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("ODDS_API_KEY", "")
        self._last_call_ts = 0.0  # for per-second throttle
        self._rate_limit_remaining: Optional[int] = None
        self._rate_limit_total: Optional[int] = None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def rate_limit_remaining(self) -> Optional[int]:
        return self._rate_limit_remaining

    @property
    def rate_limit_total(self) -> Optional[int]:
        return self._rate_limit_total

    # ── Fetch methods ──────────────────────────────────────────────────────

    def fetch_events(
        self,
        sport_keys: Optional[List[str]] = None,
        days_ahead: int = 7,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch upcoming events from The Odds API.

        The /events endpoint is free (zero usage credits).

        Args:
            sport_keys: Odds API sport keys to fetch (e.g. ['soccer_epl']).
                        If None, fetches for all supported sports.
            days_ahead: Number of days ahead to fetch events for.

        Returns:
            Dict mapping odds_sport_key → list of event dicts.
            Each event dict has keys: id, sport_key, commence_time,
            home_team, away_team.
        """
        results: Dict[str, List[Dict[str, Any]]] = {}

        if not self.is_configured:
            logger.warning("OddsAPISource: ODDS_API_KEY not set — skipping")
            return results

        if sport_keys is None:
            sport_keys = list(SPORT_KEY_MAP.keys())

        for sk in sport_keys:
            if sk not in SPORT_KEY_MAP:
                continue

            events = self._get_paginated(
                f"/sports/{sk}/events",
                params={"dateFormat": "iso"},
            )
            results[sk] = events

        return results

    def fetch_odds(
        self,
        sport_keys: Optional[List[str]] = None,
        regions: str = "uk",
        markets: str = _DEFAULT_MARKETS,
        odds_format: str = "decimal",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch current odds for upcoming events.

        COST: 1 credit per market per region per sport_key call.
        With h2h,totals × 1 region = 2 credits per sport.

        Args:
            sport_keys: Odds API sport keys. If None, uses only the
                        events that were previously fetched and are
                        still scheduled.
            regions: Bookmaker regions (uk, us, eu, au).
            markets: Comma-delimited market keys (h2h, totals, spreads).
            odds_format: 'decimal' or 'american'.

        Returns:
            Dict mapping odds_sport_key → list of odds event dicts.
            Each dict has: id, sport_key, commence_time, home_team,
            away_team, bookmakers[{key, markets[{key, outcomes[{name,price}]}]}]
        """
        results: Dict[str, List[Dict[str, Any]]] = {}

        if not self.is_configured:
            return results

        if sport_keys is None:
            sport_keys = list(SPORT_KEY_MAP.keys())

        for sk in sport_keys:
            if sk not in SPORT_KEY_MAP:
                continue

            data = self._get_json(
                f"/sports/{sk}/odds",
                params={
                    "regions": regions,
                    "markets": markets,
                    "oddsFormat": odds_format,
                },
            )
            if data is not None:
                results[sk] = data if isinstance(data, list) else [data]

        return results

    def fetch_scores(
        self,
        sport_keys: Optional[List[str]] = None,
        days_from: int = 3,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch completed match scores.

        COST: 1 credit (2 if daysFrom > 1).

        Args:
            sport_keys: Odds API sport keys.
            days_from: Days in the past to fetch completed games.

        Returns:
            Dict mapping odds_sport_key → list of score event dicts.
            Each dict has: id, sport_key, commence_time, home_team,
            away_team, scores (if completed), completed (bool).
        """
        results: Dict[str, List[Dict[str, Any]]] = {}

        if not self.is_configured:
            return results

        if sport_keys is None:
            sport_keys = list(SPORT_KEY_MAP.keys())

        for sk in sport_keys:
            if sk not in SPORT_KEY_MAP:
                continue

            data = self._get_json(
                f"/sports/{sk}/scores",
                params={"daysFrom": str(days_from), "dateFormat": "iso"},
            )
            if data is not None:
                results[sk] = data if isinstance(data, list) else [data]

        return results

    def refresh_rate_limits(self):
        """Reset monthly rate limits if it's a new month."""
        from core.kai_betting.db import get_db
        now = datetime.now(timezone.utc)
        with get_db() as db:
            row = db.execute(
                "SELECT value FROM betting_config WHERE key = 'last_rate_limit_reset'"
            ).fetchone()
            last_reset = row["value"] if row else ""

            if last_reset:
                try:
                    last_dt = datetime.fromisoformat(last_reset)
                    if last_dt.month == now.month and last_dt.year == now.year:
                        # Same month — carry over stored remaining
                        ds = db.execute(
                            "SELECT rate_limit_remaining, rate_limit_total FROM data_sources WHERE name = 'odds_api'"
                        ).fetchone()
                        if ds:
                            self._rate_limit_remaining = ds["rate_limit_remaining"]
                            self._rate_limit_total = ds["rate_limit_total"]
                        return
                except (ValueError, OSError):
                    pass

            # New month — reset to full quota
            self._rate_limit_remaining = 500
            self._rate_limit_total = 500
            db.execute("""
                INSERT OR REPLACE INTO betting_config (key, value) VALUES ('last_rate_limit_reset', ?)
            """, (now.isoformat(),))

            # Ensure data_sources row exists
            db.execute("""
                INSERT OR IGNORE INTO data_sources (name, provider, endpoint, api_status,
                    rate_limit_remaining, rate_limit_total, last_check_at)
                VALUES ('odds_api', 'the-odds-api', 'https://api.the-odds-api.com/v4',
                    'healthy', 500, 500, ?)
            """, (now.isoformat(),))
            db.execute("""
                UPDATE data_sources SET rate_limit_remaining = 500, rate_limit_total = 500,
                last_check_at = ?, api_status = 'healthy'
                WHERE name = 'odds_api'
            """, (now.isoformat(),))
            db.commit()

    def has_quota(self) -> bool:
        """Check if we have remaining API credits."""
        if self._rate_limit_remaining is None:
            self.refresh_rate_limits()
        return (self._rate_limit_remaining or 0) > 0

    def mark_api_call(self, request_count: int = 1):
        """Decrement the rate limit counter after an API call and persist."""
        if self._rate_limit_remaining is not None:
            self._rate_limit_remaining = max(0, self._rate_limit_remaining - request_count)

        try:
            from core.kai_betting.db import get_db
            now = datetime.now(timezone.utc).isoformat()
            with get_db() as db:
                db.execute("""
                    UPDATE data_sources SET rate_limit_remaining = ?,
                    last_check_at = ?, api_status = ?
                    WHERE name = 'odds_api'
                """, (self._rate_limit_remaining or 0, now,
                      "healthy" if (self._rate_limit_remaining or 0) > 0 else "rate_limited"))
                db.commit()
        except Exception:
            pass  # DB may not be initialized yet; non-critical

    # ── Internal HTTP helpers ──────────────────────────────────────────────

    def _rate_limit_sleep(self):
        """Enforce 1 request/second throttle."""
        now = time.monotonic()
        elapsed = now - self._last_call_ts
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_call_ts = time.monotonic()

    def _get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[Any]:
        """Make a GET request and return parsed JSON, or None on failure."""
        url = f"{self.BASE_URL}{path}"
        request_params: Dict[str, str] = {"apiKey": self._api_key}
        if params:
            request_params.update(params)

        self._rate_limit_sleep()

        try:
            resp = requests.get(url, params=request_params, timeout=30)

            # Track rate-limit headers
            remaining = resp.headers.get("x-requests-remaining")
            used = resp.headers.get("x-requests-used")
            if remaining is not None:
                self._rate_limit_remaining = int(remaining)
            if used is not None:
                self._rate_limit_total = int(used) + (self._rate_limit_remaining or 0)

            if resp.status_code == 401:
                logger.error("OddsAPISource: invalid API key (401)")
                return None
            if resp.status_code == 429:
                logger.warning("OddsAPISource: rate limited (429)")
                self._rate_limit_remaining = 0
                self.mark_api_call(0)
                return None
            if resp.status_code == 422:
                logger.warning(f"OddsAPISource: unprocessable request (422) — sport may be out of season: {path}")
                return None
            if not resp.ok:
                if resp.status_code == 404:
                    logger.debug(f"OddsAPISource: HTTP 404 from {path} (out of season)")
                else:
                    logger.error(f"OddsAPISource: HTTP {resp.status_code} from {path}")
                return None

            data = resp.json()
            self.mark_api_call(1)
            return data

        except requests.RequestException as e:
            logger.error(f"OddsAPISource: request failed for {path}: {e}")
            return None

    def _get_paginated(self, path: str, params: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """GET a paginated endpoint and return all results."""
        data = self._get_json(path, params)
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Some responses wrap results in a "data" key
            return data.get("data", [data])
        return []
