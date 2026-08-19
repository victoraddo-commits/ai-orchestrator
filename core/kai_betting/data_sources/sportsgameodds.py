"""SportsGameOdds — supplemental sports data provider.

Free ("amateur") tier: 10 req/min, 2500 "entities"/month (roughly one
entity per event returned). A single /v2/events call returns events,
odds, AND results together — richer than either existing provider,
which need separate calls for odds vs. scores.

The platform's ENTIRE league catalog (confirmed live against /v2/leagues,
not assumed) is only 8 leagues — no EPL/La Liga/Serie A/Bundesliga/Ligue 1/
Europa League exist here, only UEFA Champions League among marquee
European soccer competitions.

Endpoint summary:
  GET /v2/events    — events + odds + results in one call
  GET /v2/leagues    — league catalog
  GET /v2/account/usage — quota usage
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# ── League Mapping ──────────────────────────────────────────────────────────
# Confirmed against the live /v2/leagues catalog (unfiltered) plus direct
# probes of guessed IDs (EPL, LA_LIGA, SERIE_A, BUNDESLIGA, LIGUE_1,
# EUROPA_LEAGUE, etc. all return success:false/empty) — this is the
# platform's COMPLETE league catalog, not a curated subset.

LEAGUE_ID_MAP: Dict[str, str] = {
    "football": "UEFA_CHAMPIONS_LEAGUE",  # soccer, in Kai's internal naming
    "soccer": "UEFA_CHAMPIONS_LEAGUE",
    "basketball": "NBA",
    "baseball": "MLB",
    "ice_hockey": "NHL",
    "american_football": "NFL",
}

# A single Kai sport can map to more than one SportsGameOdds league
# (e.g. basketball → NBA + NCAAB). Additional leagueIDs per Kai sport key.
_EXTRA_LEAGUE_IDS: Dict[str, List[str]] = {
    "football": ["MLS"],
    "soccer": ["MLS"],
    "basketball": ["NCAAB"],
    "american_football": ["NCAAF"],
}


def league_ids_for(kai_sport: str) -> List[str]:
    """Return all SportsGameOdds leagueIDs for a given Kai sport key."""
    primary = LEAGUE_ID_MAP.get(kai_sport)
    if not primary:
        return []
    ids = [primary] + _EXTRA_LEAGUE_IDS.get(kai_sport, [])
    return ids


# Reverse map: SportsGameOdds leagueID → Kai sport key
_LEAGUE_TO_KAI: Dict[str, str] = {}
for _kai_sport, _primary_league in LEAGUE_ID_MAP.items():
    _LEAGUE_TO_KAI.setdefault(_primary_league, _kai_sport)
for _kai_sport, _extra_leagues in _EXTRA_LEAGUE_IDS.items():
    for _league in _extra_leagues:
        _LEAGUE_TO_KAI.setdefault(_league, _kai_sport)


def kai_sport_for_league(league_id: str) -> Optional[str]:
    """Return the Kai sport key for a SportsGameOdds leagueID."""
    return _LEAGUE_TO_KAI.get(league_id)


# ── Market Mapping ─────────────────────────────────────────────────────────
# Restricted to what the app already predicts (mirrors odds_api.py's
# h2h/totals scope) — player props and other periods are ignored.
#
# Live-verified against real oddID keys (not assumed): the platform's
# moneyline betTypeID is "ml" (2-way, home/away only — no draw side exists
# for soccer here, confirmed across 50+ sampled Champions League/MLS events),
# not "ml3way" as originally assumed. "match_result" selections are
# whatever sides are present (the app's PredictionEngine already handles
# 2- or 3-way match_result markets generically), so this is used as-is.

MARKET_MAP = {
    "ml": "match_result",
    "ou": "over_under",
}

_ALLOWED_PERIOD_IDS = {"game"}

# Prop markets (e.g. "firstToScore", "fieldGoals_made", "passing_yards")
# reuse the exact same periodID/betTypeID/sideID signature as the real
# full-game moneyline/totals (e.g. "firstToScore-home-game-ml-home" is
# indistinguishable from "points-home-game-ml-home" except for statID) —
# confirmed via real NFL fixture data. Only "points" is the actual
# match-result/total stat; everything else must be excluded.
_ALLOWED_STAT_IDS = {"points"}


def _american_to_decimal(american: Any) -> Optional[float]:
    """Convert American-format odds (e.g. '+279', '-139') to decimal odds."""
    if american is None:
        return None
    try:
        n = float(american)
    except (TypeError, ValueError):
        return None
    if n == 0:
        return None
    if n > 0:
        return 1 + n / 100
    return 1 + 100 / abs(n)


def _parse_odd_id(odd_id: str) -> Optional[Dict[str, str]]:
    """Parse an oddID like 'points-home-game-ml-home' into parts.

    Format: <statID>-<statEntityID>-<periodID>-<betTypeID>-<sideID>
    """
    parts = odd_id.split("-")
    if len(parts) < 5:
        return None
    return {
        "statID": "-".join(parts[:-4]),
        "statEntityID": parts[-4],
        "periodID": parts[-3],
        "betTypeID": parts[-2],
        "sideID": parts[-1],
    }


def extract_markets(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract predictable markets from a SportsGameOdds event.

    Returns a list of dicts: {market_type, selection, odds (decimal), line, bookmaker}
    Uses the consensus bookOdds (fallback fairOdds) as price — the free
    tier's byBookmaker list doesn't include Bet365/1xbet (the app's
    FREE_TIER_BOOKMAKERS), only US-facing books, so no single-bookmaker
    price is reliably present.
    """
    markets: List[Dict[str, Any]] = []
    odds = event.get("odds")
    if not isinstance(odds, dict):
        return markets

    for odd_id, odd in odds.items():
        if not isinstance(odd, dict):
            continue
        parsed = _parse_odd_id(odd_id)
        if not parsed:
            continue
        if parsed["statID"] not in _ALLOWED_STAT_IDS:
            continue
        if parsed["periodID"] not in _ALLOWED_PERIOD_IDS:
            continue
        market_type = MARKET_MAP.get(parsed["betTypeID"])
        if not market_type:
            continue
        # over/under totals come in match-total ("all") and team-total
        # ("home"/"away") flavors under the same betTypeID — only the
        # match total maps to Kai's over_under market.
        if market_type == "over_under" and parsed["statEntityID"] != "all":
            continue

        price_raw = odd.get("bookOdds") or odd.get("fairOdds")
        decimal_odds = _american_to_decimal(price_raw)
        if not decimal_odds:
            continue

        side_id = parsed["sideID"]
        line: Optional[float] = None
        if market_type == "match_result":
            selection = {"home": "home", "away": "away", "draw": "draw"}.get(side_id, side_id)
        elif market_type == "over_under":
            selection = {"over": "over", "under": "under"}.get(side_id, side_id)
            line_raw = odd.get("bookOverUnder") or odd.get("fairOverUnder")
            try:
                line = float(line_raw) if line_raw is not None else None
            except (TypeError, ValueError):
                line = None
        else:
            selection = side_id

        markets.append({
            "market_type": market_type,
            "selection": selection,
            "odds": decimal_odds,
            "line": line,
            "bookmaker": "sportsgameodds_consensus",
        })

    return markets


def extract_result(event: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Extract the final score from a finalized SportsGameOdds event.

    Returns {"home_score": int, "away_score": int}, or None if the event
    isn't finalized yet or scores aren't present.
    """
    status = event.get("status", {}) or {}
    if not status.get("finalized"):
        return None

    results = event.get("results", {}) or {}
    game = results.get("game", {}) or {}
    home_score = game.get("home", {}).get("points")
    away_score = game.get("away", {}).get("points")
    if home_score is None or away_score is None:
        return None

    try:
        return {"home_score": int(home_score), "away_score": int(away_score)}
    except (TypeError, ValueError):
        return None


# ── SportsGameOdds Client ────────────────────────────────────────────────────

class SportsGameOddsSource:
    """Client for SportsGameOdds v2.

    Reads API key from the SPORTSGAMEODDS_API_KEY environment variable.
    Tracks monthly "entity" usage (roughly 1 entity per event returned)
    in the data_sources DB table. Never throws — returns [] / {} on error.
    """

    BASE_URL = "https://api.sportsgameodds.com/v2"
    MONTHLY_ENTITY_QUOTA = 2500
    QUOTA_STOP_THRESHOLD = 2300  # stop making calls once usage reaches this

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("SPORTSGAMEODDS_API_KEY", "")
        self._last_call_ts = 0.0
        self._entities_used: Optional[int] = None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # ── Fetch methods ──────────────────────────────────────────────────────

    def fetch_events(
        self,
        league_ids: List[str],
        odds_available: bool = True,
        finalized: Optional[bool] = None,
        starts_after: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch events (with odds + results embedded) for the given leagues.

        leagueID accepts comma-separated values, so this is a single call
        per invocation regardless of how many leagues are requested.

        starts_after (ISO date/datetime string): without it, a
        finalized=true query returns EVERY finalized event the platform has
        ever recorded — including years-old games — which is never what a
        caller settling recent predictions wants.
        """
        if not self.is_configured or not league_ids:
            return []
        if not self.has_quota():
            logger.warning("SportsGameOddsSource: monthly entity quota exhausted — skipping")
            return []

        params: Dict[str, str] = {"leagueID": ",".join(league_ids)}
        if odds_available:
            params["oddsAvailable"] = "true"
        if finalized is not None:
            params["finalized"] = "true" if finalized else "false"
        if starts_after:
            params["startsAfter"] = starts_after

        data = self._get_json("/events", params=params)
        if data is None:
            return []
        events = data.get("data", []) if isinstance(data, dict) else data
        if not isinstance(events, list):
            return []
        self.mark_api_call(len(events))
        return events

    # ── Quota tracking ───────────────────────────────────────────────────────

    def refresh_rate_limits(self):
        """Reset monthly entity usage if it's a new month."""
        from core.kai_betting.db import get_db
        now = datetime.now(timezone.utc)
        with get_db() as db:
            row = db.execute(
                "SELECT value FROM betting_config WHERE key = 'sportsgameodds_rate_limit_reset'"
            ).fetchone()
            last_reset = row["value"] if row else ""

            if last_reset:
                try:
                    last_dt = datetime.fromisoformat(last_reset)
                    if last_dt.month == now.month and last_dt.year == now.year:
                        ds = db.execute(
                            "SELECT rate_limit_remaining FROM data_sources WHERE name = 'sportsgameodds'"
                        ).fetchone()
                        if ds and ds["rate_limit_remaining"] is not None:
                            self._entities_used = self.MONTHLY_ENTITY_QUOTA - ds["rate_limit_remaining"]
                        return
                except (ValueError, OSError):
                    pass

            # New month — reset usage to zero
            self._entities_used = 0
            db.execute("""
                INSERT OR REPLACE INTO betting_config (key, value)
                VALUES ('sportsgameodds_rate_limit_reset', ?)
            """, (now.isoformat(),))

            db.execute("""
                INSERT OR IGNORE INTO data_sources (name, provider, endpoint, api_status,
                    rate_limit_remaining, rate_limit_total, last_check_at)
                VALUES ('sportsgameodds', 'sportsgameodds', 'https://api.sportsgameodds.com/v2',
                    'healthy', ?, ?, ?)
            """, (self.MONTHLY_ENTITY_QUOTA, self.MONTHLY_ENTITY_QUOTA, now.isoformat()))
            db.execute("""
                UPDATE data_sources SET rate_limit_remaining = ?, rate_limit_total = ?,
                last_check_at = ?, api_status = 'healthy'
                WHERE name = 'sportsgameodds'
            """, (self.MONTHLY_ENTITY_QUOTA, self.MONTHLY_ENTITY_QUOTA, now.isoformat()))
            db.commit()

    def has_quota(self) -> bool:
        if self._entities_used is None:
            self.refresh_rate_limits()
        return (self._entities_used or 0) < self.QUOTA_STOP_THRESHOLD

    def mark_api_call(self, entity_count: int = 1):
        """Increment entity usage after a call and persist remaining quota."""
        if self._entities_used is None:
            self._entities_used = 0
        self._entities_used += entity_count
        remaining = max(0, self.MONTHLY_ENTITY_QUOTA - self._entities_used)

        try:
            from core.kai_betting.db import get_db
            now = datetime.now(timezone.utc).isoformat()
            with get_db() as db:
                db.execute("""
                    UPDATE data_sources SET rate_limit_remaining = ?,
                    last_check_at = ?, api_status = ?
                    WHERE name = 'sportsgameodds'
                """, (remaining, now,
                      "healthy" if remaining > 0 else "rate_limited"))
                db.commit()
        except Exception:
            pass  # DB may not be initialized yet; non-critical

    # ── Internal HTTP helpers ──────────────────────────────────────────────

    def _rate_limit_sleep(self):
        """Enforce a conservative throttle well under the 10 req/min cap."""
        now = time.monotonic()
        elapsed = now - self._last_call_ts
        min_interval = 6.5  # ~9 req/min, under the 10/min cap
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call_ts = time.monotonic()

    def _get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[Any]:
        url = f"{self.BASE_URL}{path}"
        request_params: Dict[str, str] = {"apiKey": self._api_key}
        if params:
            request_params.update(params)

        self._rate_limit_sleep()

        try:
            resp = requests.get(url, params=request_params, timeout=30)

            if resp.status_code == 401:
                logger.error("SportsGameOddsSource: invalid API key (401)")
                return None
            if resp.status_code == 429:
                logger.warning("SportsGameOddsSource: rate limited (429)")
                return None
            if not resp.ok:
                logger.error(f"SportsGameOddsSource: HTTP {resp.status_code} from {path}")
                return None

            data = resp.json()
            if isinstance(data, dict) and data.get("success") is False:
                logger.warning(f"SportsGameOddsSource: API returned success=false for {path}")
                return None
            return data

        except requests.RequestException as e:
            logger.error(f"SportsGameOddsSource: request failed for {path}: {e}")
            return None
