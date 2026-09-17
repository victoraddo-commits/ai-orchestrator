"""KAI Bet — screening engine (§6).

Independent, deterministic probability models (Poisson match model + recent
form) so KAI's probabilities are NOT merely a copy of the market price. This is
what lets real edges exist. Pure maths — no I/O, no external intelligence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DEFAULT_MAX_GOALS = 12
HOME_BOOST = 1.10
AWAY_FACTOR = 0.95


def _poisson(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def score_matrix(home_rate: float, away_rate: float, max_goals: int = DEFAULT_MAX_GOALS) -> List[List[float]]:
    h = [_poisson(i, home_rate) for i in range(max_goals + 1)]
    a = [_poisson(j, away_rate) for j in range(max_goals + 1)]
    return [[h[i] * a[j] for j in range(max_goals + 1)] for i in range(max_goals + 1)]


def markets_from_rates(home_rate: float, away_rate: float,
                       max_goals: int = DEFAULT_MAX_GOALS,
                       total_lines: Tuple[float, ...] = (0.5, 1.5, 2.5, 3.5, 4.5)) -> Dict:
    """Independent probabilities for 1X2 / totals / BTTS from expected goals."""
    m = score_matrix(home_rate, away_rate, max_goals)
    home = sum(m[i][j] for i in range(max_goals + 1) for j in range(max_goals + 1) if i > j)
    draw = sum(m[i][i] for i in range(max_goals + 1))
    away = sum(m[i][j] for i in range(max_goals + 1) for j in range(max_goals + 1) if i < j)
    totals = {}
    for line in total_lines:
        over = sum(m[i][j] for i in range(max_goals + 1) for j in range(max_goals + 1) if i + j > line)
        totals[line] = {"over": over, "under": 1.0 - over}
    btts = sum(m[i][j] for i in range(1, max_goals + 1) for j in range(1, max_goals + 1))
    double_chance = {"1X": home + draw, "X2": draw + away, "12": home + away}
    return {"1": home, "X": draw, "2": away, "btts_yes": btts,
            "btts_no": 1.0 - btts, "totals": totals, "double_chance": double_chance}


def team_rates(goals_for_pg: float, goals_against_pg: float, league_avg_pg: float,
               home: bool) -> float:
    """Expected goals for one team from its attack/defence vs the league average."""
    base = league_avg_pg if league_avg_pg and league_avg_pg > 0 else 1.35
    attack = (goals_for_pg / base) if goals_for_pg else 1.0
    defence = (goals_against_pg / base) if goals_against_pg else 1.0
    # blend attack/defence with the league baseline, then apply venue factor
    lam = base * ((attack + defence) / 2.0)
    lam *= (HOME_BOOST if home else AWAY_FACTOR)
    return max(0.05, min(lam, 6.0))


def expected_goals_for_match(home_gf: float, home_ga: float, away_gf: float, away_ga: float,
                             league_avg_team: float,
                             home_boost: float = HOME_BOOST,
                             away_factor: float = AWAY_FACTOR) -> Tuple[float, float]:
    """Standard attack x defence Poisson expectation for a fixture.

    home_λ = (home attack) x (away defence) x league baseline x home boost
    away_λ = (away attack) x (home defence) x league baseline x away factor
    This (unlike a simple average) properly rates strong attackers/defenders.
    """
    base = league_avg_team if league_avg_team and league_avg_team > 0 else 1.35
    home_lam = (home_gf / base) * (away_ga / base) * base * home_boost
    away_lam = (away_gf / base) * (home_ga / base) * base * away_factor
    return max(0.05, min(home_lam, 6.0)), max(0.05, min(away_lam, 6.0))


def form_points(results: List[str], weights: Optional[List[float]] = None) -> float:
    """Points-per-game from recent results (most recent first), 'W'/'D'/'L'.

    Opponent adjustment is handled by the caller; this is the raw form input.
    """
    if not results:
        return 0.0
    val = {"W": 3.0, "D": 1.0, "L": 0.0}
    if weights is None:
        weights = [1.0 / (1 + i) for i in range(len(results))]  # recency decay
    num = sum(val.get(r.upper(), 0.0) * w for r, w in zip(results, weights))
    den = sum(weights[:len(results)])
    return round(num / den, 4) if den else 0.0


def blend(stat_prob: float, market_prob: float, weight_stat: float = 0.5) -> float:
    """Blend an independent statistical probability with the market.

    `weight_stat` (0..1) is how much KAI's independent estimate is trusted when
    the two disagree. Higher weight yields larger, real edges; 0 = copy market.
    """
    w = max(0.0, min(1.0, weight_stat))
    p = w * stat_prob + (1.0 - w) * market_prob
    return max(0.001, min(0.999, p))


def selection_probability(markets: Dict, market_type: str, selection: str,
                           line: Optional[float] = None) -> Optional[float]:
    """Map a (market_type, selection[, line]) to an independent probability."""
    mt = (market_type or "").lower()
    s = (selection or "").lower()
    if mt in ("1x2", "match_result"):
        key = {"home": "1", "1": "1", "draw": "X", "x": "X", "away": "2", "2": "2"}.get(s)
        return markets.get(key) if key else None
    if mt == "double_chance":
        key = s.replace(" ", "").replace("/", "").upper()
        return markets.get("double_chance", {}).get(key)
    if mt in ("over_under", "totals"):
        if line is None:
            return None
        t = markets.get("totals", {}).get(float(line))
        if not t:
            return None
        return t["over"] if s.startswith("over") else t["under"]
    if mt in ("btts", "gg_ng", "both_teams_to_score"):
        return markets.get("btts_yes") if s in ("yes", "gg", "btts_yes") else markets.get("btts_no")
    return None
