"""Odds-API.io v3 — primary sports data provider for Kai Betting.

Covers 30+ sports with events, leagues, bookmakers, and full market discovery.
Free tier: 2 bookmakers (Bet365 + 1xbet), generous rate limits.

Endpoint summary:
  GET /v3/sports                         — free, list all sports
  GET /v3/bookmakers                     — free, list all bookmakers
  GET /v3/leagues?sport={slug}           — requires apiKey
  GET /v3/events?sport={slug}&days={n}   — requires apiKey
  GET /v3/odds?eventId={id}&bookmakers=  — requires apiKey, ALL markets

API key is read from Kai's provider_secrets.json (provider: odds_api_io),
with env var ODDS_API_IO_KEY as fallback, then legacy ODDSAPIKEY.
"""

from __future__ import annotations

import os
import time
import json
import logging
from typing import Optional, Dict, List, Any, Tuple, Set
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

# ── API Base ───────────────────────────────────────────────────────────────────

BASE_URL = "https://api.odds-api.io/v3"

# ── Sport Key Mapping ──────────────────────────────────────────────────────────
# Odds-API.io sport slugs → Kai internal sport keys

SPORT_SLUG_MAP: Dict[str, str] = {
    "football": "football",
    "basketball": "basketball",
    "tennis": "tennis",
    "baseball": "baseball",
    "american-football": "american_football",
    "ice-hockey": "ice_hockey",
    "esports": "esports",
    "darts": "darts",
    "mixed-martial-arts": "mma",
    "boxing": "boxing",
    "handball": "handball",
    "volleyball": "volleyball",
    "snooker": "snooker",
    "table-tennis": "table_tennis",
    "rugby": "rugby",
    "cricket": "cricket",
    "water-polo": "water_polo",
    "futsal": "futsal",
    "beach-volleyball": "beach_volleyball",
    "aussie-rules": "aussie_rules",
    "floorball": "floorball",
    "squash": "squash",
    "beach-soccer": "beach_soccer",
    "lacrosse": "lacrosse",
    "curling": "curling",
    "padel": "padel",
    "bandy": "bandy",
    "gaelic-football": "gaelic_football",
    "badminton": "badminton",
    "golf": "golf",
    "cycling": "cycling",
    "athletics": "athletics",
}

# Reverse map
_KAI_TO_SLUG: Dict[str, List[str]] = {}
for _slug, _kai in SPORT_SLUG_MAP.items():
    _KAI_TO_SLUG.setdefault(_kai, []).append(_slug)


def slug_for_kai(kai_sport: str) -> Optional[str]:
    """Get the primary Odds-API.io slug for a Kai sport key."""
    # For most sports the slug and kai key are the same except hyphens
    if kai_sport in SPORT_SLUG_MAP.values():
        for slug, kai in SPORT_SLUG_MAP.items():
            if kai == kai_sport:
                return slug
    return None


# ── Market Normalization ───────────────────────────────────────────────────────
# Maps Odds-API.io market names → Kai internal market types

MARKET_NORMALIZATION: Dict[str, Tuple[str, str]] = {
    # (odds_api_name, (kai_market_type, display_name))
    "ML": ("match_result", "Match Result"),
    "Double Chance": ("double_chance", "Double Chance"),
    "Draw No Bet": ("draw_no_bet", "Draw No Bet"),
    "Totals": ("over_under", "Over/Under Goals"),
    "Goals Over/Under": ("over_under", "Goal Line"),
    "Spread": ("handicap", "Handicap"),
    "Both Teams To Score": ("btts", "Both Teams To Score"),
    "Team Total Home": ("team_goals_home", "Home Team Goals"),
    "Team Total Away": ("team_goals_away", "Away Team Goals"),
    "ML HT": ("ht_result", "Half-Time Result"),
    "Spread HT": ("ht_handicap", "Half-Time Handicap"),
    "Totals HT": ("ht_goals", "Half-Time Goals"),
    "ML 2H": ("2h_result", "2nd Half Result"),
    "Totals 2H": ("2h_goals", "2nd Half Goals"),
    "Both Teams To Score HT": ("ht_btts", "Half-Time BTTS"),
    "Both Teams To Score 2H": ("2h_btts", "2nd Half BTTS"),
    "Double Chance HT": ("ht_double_chance", "Half-Time Double Chance"),
    "Half Time Result": ("ht_result", "Half-Time Result"),
    "Half Time / Full Time": ("ht_ft", "Half-Time/Full-Time"),
    "European Handicap": ("european_handicap", "European Handicap"),
    "Corners Spread": ("corners_handicap", "Corners Handicap"),
    "Corners Totals": ("corners_over_under", "Corners Over/Under"),
    "Corners Totals Home": ("corners_home", "Home Corners"),
    "Corners Totals Away": ("corners_away", "Away Corners"),
    "Corners Totals HT": ("corners_ht", "Half-Time Corners"),
    "Odd/Even": ("odd_even", "Odd/Even"),
    "Odd/Even HT": ("odd_even_ht", "Half-Time Odd/Even"),
    "Number of Goals In Match": ("exact_goals", "Exact Total Goals"),
    "Exact Total Goals": ("exact_goals", "Exact Total Goals"),
    "Correct Score": ("correct_score", "Correct Score"),
    "Correct Score HT": ("correct_score_ht", "Half-Time Correct Score"),
    "Home Team Exact Goals": ("home_exact_goals", "Home Exact Goals"),
    "Away Team Exact Goals": ("away_exact_goals", "Away Exact Goals"),
    "Team Total Goals Home": ("team_goals_home", "Home Team Goals"),
    "Team Total Goals Away": ("team_goals_away", "Away Team Goals"),
    "Clean Sheet Home": ("clean_sheet_home", "Home Clean Sheet"),
    "Clean Sheet Away": ("clean_sheet_away", "Away Clean Sheet"),
    "First Team To Score": ("first_to_score", "First Team To Score"),
    "Teams to Score": ("teams_to_score", "Teams To Score"),
    "To Qualify": ("to_qualify", "To Qualify"),
    "Winning Margin": ("winning_margin", "Winning Margin"),
    "First 10 Minutes (00:00 - 09:59)": ("first_10_min", "First 10 Minutes"),
    "Anytime Goalscorer": ("anytime_scorer", "Anytime Goalscorer"),
}

# Markets that Kai's prediction engine can actually analyze
PREDICTABLE_MARKETS: Set[str] = {
    "match_result", "double_chance", "draw_no_bet", "over_under",
    "btts", "team_goals_home", "team_goals_away",
    "ht_result", "ht_double_chance", "ht_goals",
    "2h_result", "2h_goals",
    "odd_even", "odd_even_ht",
    "handicap", "european_handicap",
    "clean_sheet_home", "clean_sheet_away",
    "first_to_score", "teams_to_score",
    "corners_over_under", "corners_handicap",
}

# ── Selection Normalization ────────────────────────────────────────────────────

def normalize_selection(market_name: str, outcome_key: str,
                         home: str, away: str) -> str:
    """Normalize odds outcome keys to Kai selection identifiers."""
    kl = outcome_key.lower().strip()

    # ML / Match Result
    if kl == "home":
        return "home"
    if kl == "away":
        return "away"
    if kl == "draw":
        return "draw"

    # Double Chance
    if kl in ("1x", "x2", "12"):
        return kl

    # Over/Under / Totals
    if kl == "over":
        return "over"
    if kl == "under":
        return "under"

    # BTTS
    if kl == "yes":
        return "yes"
    if kl == "no":
        return "no"

    # Clean sheet
    if kl in ("yes", "no"):
        return kl

    # Odd/Even
    if kl in ("odd", "even"):
        return kl

    # Match team names for home/away (Draw No Bet, Spread, etc.)
    if kl == home.lower():
        return "home"
    if kl == away.lower():
        return "away"

    # Generic fallback
    return kl


def market_is_predictable(kai_market_type: str) -> bool:
    """Check if Kai's engine can actually predict this market type."""
    return kai_market_type in PREDICTABLE_MARKETS


# ── SportyBet Display Mapping ──────────────────────────────────────────────────

def sportybet_display(kai_market: str, selection: str, line: Optional[float] = None) -> str:
    """Convert internal market/selection to SportyBet-friendly display text."""
    display_map = {
        ("match_result", "home"): "Home Win",
        ("match_result", "away"): "Away Win",
        ("match_result", "draw"): "Draw",
        ("double_chance", "1X"): "Double Chance — 1X",
        ("double_chance", "X2"): "Double Chance — X2",
        ("double_chance", "12"): "Double Chance — 12",
        ("draw_no_bet", "home"): "Draw No Bet — Home",
        ("draw_no_bet", "away"): "Draw No Bet — Away",
        ("btts", "yes"): "Both Teams To Score — Yes",
        ("btts", "no"): "Both Teams To Score — No",
        ("team_goals_home", "over"): f"Home Team Over {line or 1.5} Goals",
        ("team_goals_away", "over"): f"Away Team Over {line or 1.5} Goals",
        ("team_goals_home", "under"): f"Home Team Under {line or 1.5} Goals",
        ("team_goals_away", "under"): f"Away Team Under {line or 1.5} Goals",
        ("ht_result", "home"): "Half-Time — Home",
        ("ht_result", "away"): "Half-Time — Away",
        ("ht_result", "draw"): "Half-Time — Draw",
        ("ht_double_chance", "1X"): "Half-Time Double Chance — 1X",
        ("ht_double_chance", "X2"): "Half-Time Double Chance — X2",
        ("clean_sheet_home", "yes"): "Home Clean Sheet — Yes",
        ("clean_sheet_away", "yes"): "Away Clean Sheet — Yes",
        ("first_to_score", "home"): "First To Score — Home",
        ("first_to_score", "away"): "First To Score — Away",
        ("odd_even", "odd"): "Odd/Even Goals — Odd",
        ("odd_even", "even"): "Odd/Even Goals — Even",
        ("handicap", "home"): f"Handicap Home {line or ''}".strip(),
        ("handicap", "away"): f"Handicap Away {line or ''}".strip(),
    }

    key = (kai_market, selection)
    if key in display_map:
        return display_map[key]

    # Over/Under with line
    if kai_market == "over_under":
        if line:
            if selection == "over":
                return f"Over {line} Goals"
            else:
                return f"Under {line} Goals"
        return f"Over/Under Goals — {selection.title()}"

    # Corners
    if kai_market == "corners_over_under":
        if line:
            return f"Corners Over {line}" if selection == "over" else f"Corners Under {line}"
    if kai_market == "corners_handicap":
        return f"Corners Handicap — {selection.title()}"

    # Generic fallback
    market_display = kai_market.replace("_", " ").title()
    return f"{market_display} — {selection.title()}"


# ── OddsAPIio Client ───────────────────────────────────────────────────────────

class OddsAPIioSource:
    """Client for Odds-API.io v3.

    Reads API key from Kai's provider_secrets.json (provider: odds_api_io),
    with env var ODDS_API_IO_KEY as fallback, then ODDSAPIKEY as legacy fallback.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or self._resolve_key()
        self._last_call_ts = 0.0
        self._requests_used = 0
        self._requests_limit = 1000  # daily limit for free tier
        self._last_success_ts: Optional[str] = None
        self._last_error: Optional[str] = None
        self._latency_ms: float = 0.0
        self._sports_cache: Optional[List[Dict]] = None
        self._sports_cache_time: float = 0.0
        self._bookmakers_cache: Optional[List[Dict]] = None
        self._bookmakers_cache_time: float = 0.0

    @staticmethod
    def _resolve_key() -> str:
        """Resolve API key from multiple sources in priority order."""
        # 1. Kai secrets system
        try:
            from core.ai.secrets import get_api_key
            key = get_api_key("odds_api_io")
            if key:
                return key
        except Exception:
            pass

        # 2. ODDS_API_IO_KEY env var
        key = os.environ.get("ODDS_API_IO_KEY", "").strip()
        if key:
            return key

        # 3. Legacy ODDSAPIKEY
        key = os.environ.get("ODDSAPIKEY", "").strip()
        if key:
            return key

        # 4. Old ODDS_API_KEY (from the-odds-api)
        key = os.environ.get("ODDS_API_KEY", "").strip()
        if key:
            return key

        return ""

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def provider_status(self) -> Dict[str, Any]:
        return {
            "connected": self.is_configured and self._last_success_ts is not None,
            "last_success": self._last_success_ts,
            "last_error": self._last_error,
            "requests_used": self._requests_used,
            "requests_limit": self._requests_limit,
            "latency_ms": self._latency_ms,
        }

    # ── Sports ─────────────────────────────────────────────────────────────────

    def fetch_sports(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """Fetch all available sports. Cached for 1 hour (rarely changes)."""
        if use_cache and self._sports_cache and \
           time.monotonic() - self._sports_cache_time < 3600:
            return self._sports_cache

        data = self._get_json("/sports")
        if data is None:
            return self._sports_cache or []

        self._sports_cache = data if isinstance(data, list) else []
        self._sports_cache_time = time.monotonic()
        return self._sports_cache

    # ── Bookmakers ─────────────────────────────────────────────────────────────

    def fetch_bookmakers(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """Fetch all bookmakers. Cached for 24 hours."""
        if use_cache and self._bookmakers_cache and \
           time.monotonic() - self._bookmakers_cache_time < 86400:
            return self._bookmakers_cache

        data = self._get_json("/bookmakers")
        if data is None:
            return self._bookmakers_cache or []

        self._bookmakers_cache = data if isinstance(data, list) else []
        self._bookmakers_cache_time = time.monotonic()
        return self._bookmakers_cache

    # ── Leagues ────────────────────────────────────────────────────────────────

    def fetch_leagues(self, sport_slug: str) -> List[Dict[str, Any]]:
        """Fetch leagues for a sport. Requires API key."""
        if not self.is_configured:
            return []

        data = self._get_json("/leagues", params={"sport": sport_slug})
        if data is None:
            return []
        return data if isinstance(data, list) else []

    # ── Events ─────────────────────────────────────────────────────────────────

    def fetch_events(
        self,
        sport_slugs: Optional[List[str]] = None,
        days: int = 3,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch upcoming events for one or more sports.

        Args:
            sport_slugs: Odds-API.io sport slugs. Defaults to Kai active sports.
            days: Days ahead to fetch events for.

        Returns:
            Dict mapping sport_slug → list of event dicts.
            Each event: {id, home, away, homeId, awayId, date, status,
                         sport: {name, slug}, league: {name, slug}, scores?}
        """
        results: Dict[str, List[Dict[str, Any]]] = {}

        if not self.is_configured:
            logger.warning("OddsAPIioSource: API key not configured")
            return results

        if sport_slugs is None:
            sport_slugs = list(SPORT_SLUG_MAP.keys())[:10]  # top 10 sports

        for slug in sport_slugs:
            if slug not in SPORT_SLUG_MAP:
                continue

            data = self._get_json("/events", params={
                "sport": slug,
                "days": str(days),
            })
            if data is None:
                continue

            events = data if isinstance(data, list) else []
            results[slug] = events

        return results

    # ── Odds ───────────────────────────────────────────────────────────────────

    def fetch_odds(
        self,
        event_id: int,
        sport_slug: str = "football",
        bookmakers: str = "Bet365,1xbet",
    ) -> Optional[Dict[str, Any]]:
        """Fetch odds for a single event with ALL available markets.

        Args:
            event_id: Odds-API.io event ID.
            sport_slug: Sport slug for the event.
            bookmakers: Comma-separated bookmaker names (max 2 on free tier).

        Returns:
            Dict with: id, home, away, league, status, date, sport, bookmakers={}.
            Each bookmaker value is a list of market dicts:
            {name, updatedAt, odds: [{outcome_key: odds_value, ...}]}.
        """
        if not self.is_configured:
            return None

        data = self._get_json("/odds", params={
            "sport": sport_slug,
            "eventId": str(event_id),
            "bookmakers": bookmakers,
        })

        if data is None:
            return None

        if isinstance(data, dict):
            return data
        return None

    def fetch_odds_batch(
        self,
        event_ids: List[int],
        sport_slug: str = "football",
        bookmakers: str = "Bet365,1xbet",
    ) -> Dict[int, Dict[str, Any]]:
        """Fetch odds for multiple events. Returns dict of event_id → odds data."""
        results: Dict[int, Dict[str, Any]] = {}
        for eid in event_ids:
            data = self.fetch_odds(eid, sport_slug, bookmakers)
            if data:
                results[eid] = data
        return results

    # ── Market Extraction ──────────────────────────────────────────────────────

    @staticmethod
    def extract_markets(odds_data: Dict[str, Any],
                         preferred_bookmaker: str = "Bet365") -> List[Dict[str, Any]]:
        """Extract normalized markets from odds response.

        Returns a flat list of {market_type, market_name, selection, odds, line, bookmaker}
        for every available odds outcome from the preferred bookmaker.
        Falls back to any available bookmaker if preferred unavailable.
        """
        markets = []
        bms = odds_data.get("bookmakers", {})
        home = odds_data.get("home", "")
        away = odds_data.get("away", "")

        # Prefer Bet365, fall back to any available
        bookie_names = list(bms.keys())
        bookie = preferred_bookmaker if preferred_bookmaker in bookie_names else \
                 (bookie_names[0] if bookie_names else None)

        if not bookie:
            return markets

        market_list = bms.get(bookie, [])
        if not isinstance(market_list, list):
            return markets

        for market_entry in market_list:
            name = market_entry.get("name", "")
            odds_list = market_entry.get("odds", [])

            # Look up normalization
            norm = MARKET_NORMALIZATION.get(name)
            if not norm:
                continue

            kai_market, display_name = norm

            for odds_entry in odds_list:
                line = odds_entry.get("hdp")

                for outcome_key, odds_value in odds_entry.items():
                    if outcome_key == "hdp":
                        continue

                    if odds_value in ("N/A", None, ""):
                        continue

                    try:
                        decimal_odds = float(odds_value)
                    except (ValueError, TypeError):
                        continue

                    if decimal_odds <= 1.0:
                        continue

                    selection = normalize_selection(name, outcome_key, home, away)

                    markets.append({
                        "market_type": kai_market,
                        "market_name": display_name,
                        "selection": selection,
                        "odds": decimal_odds,
                        "line": float(line) if line is not None else None,
                        "bookmaker": bookie,
                        "provider_name": name,
                        "outcome_key": outcome_key,
                    })

        return markets

    # ── HTTP Layer ─────────────────────────────────────────────────────────────

    def _rate_limit_sleep(self):
        """Enforce 1 request/second throttle."""
        now = time.monotonic()
        elapsed = now - self._last_call_ts
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_call_ts = time.monotonic()

    def _get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[Any]:
        """Make a GET request and return parsed JSON, or None on failure."""
        url = f"{BASE_URL}{path}"
        request_params: Dict[str, str] = {}
        if self._api_key:
            request_params["apiKey"] = self._api_key
        if params:
            request_params.update(params)

        self._rate_limit_sleep()
        start = time.time()

        try:
            resp = requests.get(url, params=request_params, timeout=30)
            self._latency_ms = (time.time() - start) * 1000
            self._requests_used += 1

            if resp.status_code == 401:
                self._last_error = "Invalid API key (401)"
                logger.error(f"OddsAPIioSource: {self._last_error}")
                return None
            if resp.status_code == 429:
                self._last_error = "Rate limited (429)"
                logger.warning(f"OddsAPIioSource: {self._last_error}")
                return None
            if resp.status_code == 422:
                self._last_error = f"Unprocessable (422): {path}"
                logger.warning(f"OddsAPIioSource: {self._last_error}")
                return None
            if not resp.ok:
                if resp.status_code == 404:
                    logger.debug(f"OddsAPIioSource: 404 from {path}")
                else:
                    self._last_error = f"HTTP {resp.status_code} from {path}"
                    logger.error(f"OddsAPIioSource: {self._last_error}")
                return None

            data = resp.json()
            self._last_success_ts = datetime.now(timezone.utc).isoformat()
            self._last_error = None

            if isinstance(data, dict) and "error" in data:
                self._last_error = data["error"]
                logger.error(f"OddsAPIioSource: API error — {data['error']}")
                return None

            return data

        except requests.RequestException as e:
            self._last_error = str(e)[:200]
            logger.error(f"OddsAPIioSource: request failed for {path}: {e}")
            return None

    # ── Health Check ───────────────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        """Verify the API key and connectivity."""
        if not self.is_configured:
            return {"ok": False, "status": "no_key", "detail": "API key not configured"}

        data = self._get_json("/sports")
        if data is None:
            return {"ok": False, "status": "error",
                    "detail": self._last_error or "Connection failed"}

        sports_count = len(data) if isinstance(data, list) else 0

        return {
            "ok": True,
            "status": "connected",
            "sports_available": sports_count,
            "latency_ms": self._latency_ms,
            "requests_used": self._requests_used,
        }
