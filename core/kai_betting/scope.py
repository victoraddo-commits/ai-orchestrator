"""Kai Betting — approved sports & competition scope registry.

Single source of truth for what the system may process. Every event must
pass, in order:

    SPORT filter → COMPETITION filter → tier/priority sort → data quality
    → odds filter → prediction engine

Anything that fails the sport or competition filter is rejected BEFORE it
consumes API budget, AI inference, or a DB row.

The universe is deliberately narrow:

  - football  : top-5 domestic leagues × 2 divisions (10 competitions) plus
                a fixed list of major club/international tournaments.
  - tennis    : Grand Slams, ATP/WTA Finals, Masters 1000 / WTA 1000, 500s.
  - basketball: NBA + EuroLeague, the major European/other professional
                leagues, and major FIBA tournaments.

Competitions are whitelisted by NORMALIZED SLUG (underscore→hyphen, lowercased)
— the one provider field that is stable and unique — with a small name-keyword
fallback for the continental/international tournaments whose slugs vary.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# ── Approved sports ───────────────────────────────────────────────────────────
APPROVED_SPORTS = frozenset({"football", "tennis", "basketball"})

# ── Tier codes ────────────────────────────────────────────────────────────────
# Football: S = top division, A = second division, T = tournament.
# Tennis/basketball: 1..4 = competition class, T = tournament.
_TIER_TO_DB = {"S": 1, "A": 2, "T": 3, "1": 1, "2": 2, "3": 3, "4": 3}
_DB_EXCLUDED = 99


def db_tier(tier_code: str) -> int:
    """Numeric tier for the leagues.tier column (99 = excluded)."""
    return _TIER_TO_DB.get(tier_code, _DB_EXCLUDED)


# ── Competition whitelists (normalized slug → (tier, priority)) ───────────────
_FOOTBALL: dict = {
    # England
    "epl": ("S", 100), "england-premier-league": ("S", 100), "premier-league": ("S", 100),
    "efl-championship": ("A", 80), "championship": ("A", 80),
    "england-championship": ("A", 80), "efl-champ": ("A", 80),
    # Spain
    "spain-la-liga": ("S", 100), "la-liga": ("S", 100), "laliga": ("S", 100),
    "spain-segunda-division": ("A", 80), "segunda-division": ("A", 80),
    "laliga-2": ("A", 80), "la-liga-2": ("A", 80),
    # Italy
    "italy-serie-a": ("S", 100), "serie-a": ("S", 100),
    "italy-serie-b": ("A", 80), "serie-b": ("A", 80),
    # Germany
    "germany-bundesliga": ("S", 100), "bundesliga": ("S", 100),
    "germany-2-bundesliga": ("A", 80), "2-bundesliga": ("A", 80), "bundesliga-2": ("A", 80),
    # France
    "france-ligue-1": ("S", 100), "france-ligue-one": ("S", 100), "ligue-1": ("S", 100),
    "france-ligue-2": ("A", 80), "france-ligue-two": ("A", 80), "ligue-2": ("A", 80),
}

_TENNIS: dict = {
    "australian-open": ("1", 100),
    "roland-garros": ("1", 100), "french-open": ("1", 100),
    "wimbledon": ("1", 100),
    "us-open": ("1", 100),
    "atp-finals": ("2", 90), "wta-finals": ("2", 90),
    "atp-masters-1000": ("3", 80), "masters-1000": ("3", 80),
    "wta-1000": ("3", 80),
    "atp-500": ("4", 70), "wta-500": ("4", 70),
}

_BASKETBALL: dict = {
    "nba": ("1", 100),
    "euroleague": ("1", 100),
    "acb": ("2", 80), "liga-acb": ("2", 80), "liga-endesa": ("2", 80),
    "lega-basket-serie-a": ("2", 80), "lega-basket": ("2", 80),
    "basketball-bundesliga": ("2", 80), "bbl": ("2", 80),
    "lnb-pro-a": ("2", 80), "lnb": ("2", 80),
    "turkish-basketball-super-league": ("2", 80), "bsl": ("2", 80),
    "greek-basket-league": ("2", 80), "greek-basket": ("2", 80), "a1-ethniki": ("2", 80),
    "wnba": ("2", 80),
    "nbl": ("2", 80),
    "cba": ("2", 80),
}

# Name-keyword fallbacks, uniform shape: (keyword, tier, priority). Used only
# where the slug is unreliable (tournaments, Grand Slams). Matched as a
# substring of the lowercased league name.
_FOOTBALL_NAMES: tuple = (
    ("uefa champions league", "T", 60),
    ("uefa europa league", "T", 60),
    ("uefa conference league", "T", 60),
    ("club world cup", "T", 60),
    ("fifa world cup", "T", 60),
    ("european championship", "T", 60),
    ("copa america", "T", 60),
    ("africa cup", "T", 60), ("afcon", "T", 60),
    ("uefa nations league", "T", 60),
)

_TENNIS_NAMES: tuple = (
    ("australian open", "1", 100),
    ("roland garros", "1", 100), ("french open", "1", 100),
    ("wimbledon", "1", 100),
    ("us open", "1", 100),
    ("atp finals", "2", 90), ("wta finals", "2", 90),
    ("masters 1000", "3", 80), ("atp masters", "3", 80),
    ("wta 1000", "3", 80),
    ("atp 500", "4", 70), ("wta 500", "4", 70),
)

_BASKETBALL_NAMES: tuple = (
    ("fiba world cup", "T", 60), ("fiba eurobasket", "T", 60),
    ("olympic basketball", "T", 60),
)

_COMPETITIONS = {"football": _FOOTBALL, "tennis": _TENNIS, "basketball": _BASKETBALL}
_NAME_KEYWORDS = {
    "football": _FOOTBALL_NAMES,
    "tennis": _TENNIS_NAMES,
    "basketball": _BASKETBALL_NAMES,
}

# Name keywords sorted longest-first so a specific match wins.
_NAME_KEYWORDS_SORTED = {
    sport: tuple(sorted(kws, key=lambda kt: -len(kt[0])))
    for sport, kws in _NAME_KEYWORDS.items()
}


def _normalize_slug(slug: str) -> str:
    return (slug or "").replace("_", "-").strip().lower()


def _normalize_name(name: str) -> str:
    """Lowercase + strip accents so 'Copa América' matches 'copa america'."""
    normalized = unicodedata.normalize("NFKD", name or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


# Reject-list substrings checked on the name BEFORE the accept whitelist, so a
# junior/youth/amateur/ITF/friendly event that happens to contain an approved
# keyword (e.g. "Junior - Wimbledon") is still rejected.
_REJECT_NAME_KEYWORDS: tuple = (
    "junior", "youth", "amateur", "exhibition", "friendly", "reserve",
    "u-17", "u-19", "u-20", "u-21", "u-23",
    "u17", "u19", "u20", "u21", "u23",
    "college", "ncaa", "itf", "futures", "challenger",
    "qualif", "playoff", "play-off", "preliminary", "prelim",
)


@dataclass(frozen=True)
class Classification:
    allowed: bool
    sport: str
    tier: str      # 'S'|'A'|'T' (football), '1'..'4'|'T' (other); '' if not allowed
    priority: int  # 0 if not allowed
    reason: str    # '' if allowed; else a stable reason code


def sport_allowed(sport_key: str) -> bool:
    return sport_key in APPROVED_SPORTS


def classify_competition(
    sport_key: str,
    league_name: str = "",
    league_slug: str = "",
) -> Classification:
    """Classify a league/competition for a sport.

    Returns a Classification; ``allowed`` is False for out-of-scope sports or
    competitions, with a stable ``reason`` the pipeline can log/act on.
    """
    if sport_key not in APPROVED_SPORTS:
        return Classification(False, sport_key, "", 0, "OUT_OF_SCOPE_SPORT")

    slug = _normalize_slug(league_slug)
    entry = _COMPETITIONS[sport_key].get(slug)
    if entry is not None:
        tier, priority = entry
        return Classification(True, sport_key, tier, priority, "")

    name = _normalize_name(league_name)
    if any(k in name for k in _REJECT_NAME_KEYWORDS):
        return Classification(False, sport_key, "", 0, "OUT_OF_SCOPE_COMPETITION")

    for keyword, tier, priority in _NAME_KEYWORDS_SORTED[sport_key]:
        if keyword in name:
            return Classification(True, sport_key, tier, priority, "")

    return Classification(False, sport_key, "", 0, "OUT_OF_SCOPE_COMPETITION")
