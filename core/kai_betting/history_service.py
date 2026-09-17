"""KAI Bet — cached history service + independent-probability blend.

Wraps the openfootball loader with an in-process cache (season dumps are large),
maps league names to openfootball codes, and exposes an independent probability
for a specific selection so the prediction engine can blend it with the market.

Opt-in via KAI_BET_USE_HISTORY=1 (default off) and fail-safe: any problem returns
None so predictions fall back to the previous behaviour.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

from core.kai_betting.history import (
    LEAGUES, load_league, parse_matches, independent_markets,
)
from core.kai_betting.screening import selection_probability

_CACHE: Dict[str, Tuple[float, list]] = {}
_TTL = float(os.environ.get("KAI_BET_HISTORY_TTL", "3600"))  # seconds

# league-name keyword -> openfootball code
_LEAGUE_MAP = [
    ("bundesliga", "de.1"), ("2. bundesliga", "de.2"),
    ("premier league", "en.1"), ("la liga", "es.1"),
    ("serie a", "it.1"), ("ligue 1", "fr.1"),
]


def enabled() -> bool:
    return os.environ.get("KAI_BET_USE_HISTORY", "0") == "1"


def league_code(league_name: str) -> Optional[str]:
    n = (league_name or "").lower()
    for kw, code in _LEAGUE_MAP:
        if kw in n:
            return code
    return None


def _matches_for(code: str, season: str = "2024-25") -> list:
    key = f"{code}:{season}"
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _TTL:
        return hit[1]
    doc = load_league(code, season)
    ms = parse_matches(doc) if doc else []
    _CACHE[key] = (now, ms)
    return ms


def independent_selection_prob(home: str, away: str, league_name: str,
                               market_type: str, selection: str,
                               line: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Independent probability for one selection, or None if unavailable.

    Returns {probability, sufficient, expected_home, expected_away, form}.
    """
    code = league_code(league_name)
    if not code:
        return None
    try:
        ms = _matches_for(code)
        if not ms:
            return None
        res = independent_markets(home, away, ms)
        if not res.get("sufficient"):
            return None
        p = selection_probability(res["markets"], market_type, selection, line)
        if p is None:
            return None
        return {"probability": p, "sufficient": True,
                "expected_home": res["expected_home_goals"],
                "expected_away": res["expected_away_goals"],
                "form": {"home": res["home_strength"]["form"][:5],
                         "away": res["away_strength"]["form"][:5]},
                "league_code": code}
    except Exception:  # noqa: BLE001 - never break prediction
        return None


_KNOWN_CODES = ("de.1", "en.1", "es.1", "it.1", "fr.1", "de.2")


def independent_selection_prob_anyleague(home: str, away: str, market_type: str,
                                         selection: str, line: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Try each known league (cached) until one has sufficient history for both teams."""
    for code in _KNOWN_CODES:
        try:
            ms = _matches_for(code)
            if not ms:
                continue
            res = independent_markets(home, away, ms)
            if not res.get("sufficient"):
                continue
            p = selection_probability(res["markets"], market_type, selection, line)
            if p is None:
                continue
            return {"probability": p, "league_code": code,
                    "expected_home": res["expected_home_goals"],
                    "expected_away": res["expected_away_goals"]}
        except Exception:  # noqa: BLE001
            continue
    return None
