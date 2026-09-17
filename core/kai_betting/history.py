"""KAI Bet — historical results (openfootball) → team strength → screening.

Free, no-key season dumps (github.com/openfootball/football.json) give full
league results. We derive per-team form and goals-for/against, feed those into
the screening engine, and produce INDEPENDENT probabilities (not copies of the
market). Pure parsers are unit-tested; the fetch is best-effort.
"""
from __future__ import annotations

import json
import unicodedata
import urllib.request
from typing import Any, Dict, List, Optional

from core.kai_betting.screening import markets_from_rates, expected_goals_for_match

_UA = "Mozilla/5.0 (X11; Linux x86_64) kai-betting/1.0"
_RAW = "https://raw.githubusercontent.com/openfootball/football.json/master"

# season code -> openfootball league file
LEAGUES: Dict[str, str] = {
    "de.1": "Bundesliga",
    "en.1": "Premier League",
    "es.1": "La Liga",
    "it.1": "Serie A",
    "fr.1": "Ligue 1",
    "de.2": "2. Bundesliga",
}


def load_league(code: str = "de.1", season: str = "2024-25") -> Optional[Dict[str, Any]]:
    """Fetch a season dump. Returns the parsed JSON or None."""
    url = f"{_RAW}/{season}/{code}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception:
        return None


def parse_matches(doc: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise an openfootball doc into [{home,away,hg,ag,date}], scored only."""
    out: List[Dict[str, Any]] = []
    for m in (doc or {}).get("matches", []):
        score = m.get("score") or {}
        ft = score.get("ft")
        if not ft or len(ft) != 2:
            continue
        out.append({"home": m.get("team1"), "away": m.get("team2"),
                    "hg": int(ft[0]), "ag": int(ft[1]), "date": m.get("date", "")})
    return out


def league_avg_goals(matches: List[Dict[str, Any]]) -> float:
    if not matches:
        return 1.35
    total = sum(m["hg"] + m["ag"] for m in matches)
    return round(total / len(matches), 4)  # goals per GAME (both teams)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().replace("munchen", "munich").replace("\\'", "'").strip()


_STOP = {"fc", "sc", "vfl", "vfb", "sv", "tsg", "bsc", "cf", "ac", "ss", "as",
         "us", "ii", "inc", "the", "club", "1846", "1899", "1910", "1848"}


def _tokens(s: str) -> set:
    import re as _re
    toks = _re.findall(r"[a-z]+", _norm(s))
    return {t for t in toks if t not in _STOP and len(t) > 2}


def _team_match(name: str, other: str, threshold: float = 0.6) -> bool:
    """Token-overlap match (handles 'Bayern Munich' vs 'FC Bayern München')."""
    a, b = _tokens(name), _tokens(other)
    if not a or not b:
        return _norm(name) == _norm(other)
    return (len(a & b) / min(len(a), len(b))) >= threshold


def team_strength(matches: List[Dict[str, Any]], team: str, last_n: int = 15) -> Dict[str, Any]:
    """Recent form + goals-per-game for a team from a season's matches."""
    played = [m for m in matches if _team_match(team, m["home"]) or _team_match(team, m["away"])]
    played = played[-last_n:] if last_n else played
    form, gf, ga, n = [], 0, 0, 0
    for m in played:
        home = _team_match(team, m["home"])
        f, a = (m["hg"], m["ag"]) if home else (m["ag"], m["hg"])
        gf += f
        ga += a
        n += 1
        form.append("W" if f > a else ("D" if f == a else "L"))
    form = list(reversed(form))  # most recent first
    return {"team": team, "games": n,
            "goals_for_pg": round(gf / n, 3) if n else 0.0,
            "goals_against_pg": round(ga / n, 3) if n else 0.0,
            "form": form}


def independent_markets(home: str, away: str, matches: List[Dict[str, Any]],
                        home_last_n: int = 15, away_last_n: int = 15) -> Dict[str, Any]:
    """Independent 1X2/OU/BTTS probabilities for a fixture from season results.

    `sufficient` is False when either team has no matched history — callers MUST
    NOT emit value recommendations in that case (fail-safe, §44).
    """
    avg_game = league_avg_goals(matches)          # goals per GAME (both teams)
    avg_team = avg_game / 2.0 if avg_game else 1.35  # per-team baseline
    hs = team_strength(matches, home, home_last_n)
    as_ = team_strength(matches, away, away_last_n)
    sufficient = hs["games"] >= 3 and as_["games"] >= 3
    home_rate, away_rate = expected_goals_for_match(
        hs["goals_for_pg"], hs["goals_against_pg"],
        as_["goals_for_pg"], as_["goals_against_pg"], avg_team)
    markets = markets_from_rates(home_rate, away_rate)
    return {"home_strength": hs, "away_strength": as_, "sufficient": sufficient,
            "league_avg_goals_per_game": round(avg_game, 4),
            "league_avg_goals_per_team": round(avg_team, 4),
            "expected_home_goals": round(home_rate, 3), "expected_away_goals": round(away_rate, 3),
            "markets": markets}
