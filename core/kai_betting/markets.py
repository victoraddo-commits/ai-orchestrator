"""KAI Bet — market taxonomy (learned from SportyBet, 2026-09-17).

A real catalogue of the market families SportyBet offers (imported from a live
fixture: Bayern Munich vs Union Berlin, `sr:match:72513202`, 273 distinct
market types / 1209 lines). Each family records what it needs and whether KAI
models it deterministically today.

Purpose: reasoning must reference the REAL available options, never invent them
(§44 no-hallucination). Player/team-prop families are marked `modelled=False`
until KAI ingests the required player-level data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class MarketFamily:
    key: str
    name: str
    group: str          # SportyBet group: Main|Goals|Half|Combo|Players|Minutes|Bookings|Corners|Match|Teams
    level: str          # match | team | player | combo
    modelled: bool      # KAI's deterministic model supports it today
    sportybet_ids: str  # representative market id(s)
    requires: str       # data required to model it ("" = none beyond odds)
    hint: str           # reasoning hint


FAMILIES: Dict[str, MarketFamily] = {
    "1x2": MarketFamily("1x2", "1X2 (Match Result)", "Main", "match", True, "1",
                        "", "Win/draw/win at regular time."),
    "double_chance": MarketFamily("double_chance", "Double Chance", "Main", "match", True, "10",
                                  "", "Two of three outcomes."),
    "draw_no_bet": MarketFamily("draw_no_bet", "Draw No Bet", "Main", "match", True, "11",
                                "", "Stake refunded on a draw."),
    "over_under": MarketFamily("over_under", "Over/Under (Goals)", "Main", "match", True, "18",
                               "", "Total goals line."),
    "btts": MarketFamily("btts", "Both Teams To Score / GG-NG", "Main", "match", True, "29",
                         "", "Both teams score."),
    "handicap": MarketFamily("handicap", "Handicap (European)", "Main", "match", True, "14",
                             "", "Winner with a goal head-start."),
    "asian_handicap": MarketFamily("asian_handicap", "Asian Handicap", "Main", "match", True, "16",
                                   "", "Winner with quarter/half-goal lines."),
    "correct_score": MarketFamily("correct_score", "Correct Score", "Main", "match", False, "41,45",
                                  "scoreline distribution", "Exact final score."),
    "htft": MarketFamily("htft", "Half Time / Full Time", "Half", "match", False, "46",
                         "half-split model", "HT/FT combination."),
    "first_goal": MarketFamily("first_goal", "1st / Last Goal", "Main", "match", False, "8,84",
                               "goal-timing model", "First/last scorer timing."),
    "goals_in_a_row": MarketFamily("goals_in_a_row", "Team to Score N+ Goals in a Row", "Main",
                                   "team", False, "60010,60020,60011,60021",
                                   "goal-sequence data", "A team scores 2+/3+ consecutive goals."),
    "team_over_under": MarketFamily("team_over_under", "Team Over/Under (goals)", "Goals", "team",
                                    False, "19,20", "team scoring model", "One team's goal total."),
    "btts_both_halves": MarketFamily("btts_both_halves", "Both Teams to Score in Both Halves", "Goals",
                                     "match", False, "56,57,900027", "half-split model", "Scoring both halves."),
    "goal_bounds": MarketFamily("goal_bounds", "Goal Bounds / Excluded Goals", "Goals", "match",
                                False, "450002,450003,450005,450006", "goal distribution", "Banded goal counts."),
    "shots_total": MarketFamily("shots_total", "Match Shots Over/Under", "Match", "match", False,
                                "900394,800054", "shot data (xG/shots)", "Total shots / shots outside box."),
    "team_assists": MarketFamily("team_assists", "Team Assists", "Teams", "team", False, "800262",
                                 "assist/chance-creation data", "Total assists by a team."),
    "team_shots_on_target": MarketFamily("team_shots_on_target", "Team Shots on Target", "Match",
                                         "team", False, "800287", "shot-on-target data",
                                         "A team's shots on target (per half available)."),
    "player_goalscorer": MarketFamily("player_goalscorer", "Player to Score (1st/Last/Anytime)",
                                      "Players", "player", False, "38,39,40,800109",
                                      "lineups + player scoring rates", "Player scores."),
    "player_shots": MarketFamily("player_shots", "Player Shots", "Players", "player", False, "776",
                                 "lineups + player shot rates", "A player's shot count."),
    "player_shots_on_target": MarketFamily("player_shots_on_target",
                                           "Player Shots on Target / on Goal", "Players", "player",
                                           False, "777,800285,800261,800231,800233,800234",
                                           "lineups + player SoT rates",
                                           "A player's shots on target (incl. per half, by foot)."),
    "player_assists": MarketFamily("player_assists", "Player Assists", "Players", "player", False,
                                   "770,800289,800290", "lineups + player assist rates",
                                   "A player's assists (or score/assist combos)."),
    "player_cards": MarketFamily("player_cards", "Player to be Carded", "Bookings", "player", False,
                                 "1191,800296,800118", "lineups + referee profile", "Player booked."),
    "player_saves": MarketFamily("player_saves", "Player Saves", "Players", "player", False, "800297",
                                 "lineups + GK data", "Goalkeeper saves."),
    "player_passes": MarketFamily("player_passes", "Player Accurate Passes", "Players", "player",
                                  False, "800292", "lineups + passing data", "A player's passes."),
    "player_tackles": MarketFamily("player_tackles", "Player Tackles", "Players", "player", False,
                                   "780", "lineups + tackle data", "A player's tackles."),
    "corners_over_under": MarketFamily("corners_over_under", "Corners Over/Under", "Corners", "match",
                                       False, "166,162", "corner data", "Total corners."),
    "corner_handicap": MarketFamily("corner_handicap", "Corner Handicap / 1X2", "Corners", "team",
                                    False, "165,176,162", "corner data", "Corner winner/line."),
    "match_cards": MarketFamily("match_cards", "Match Cards", "Match", "match", False, "800063,800060",
                                "card data + referee", "Total cards."),
    "minutes": MarketFamily("minutes", "Time-Interval Markets", "Minutes", "match", False,
                            "100,101,900069,900313", "in-play timing model", "Goal/result by minute band."),
    "combo": MarketFamily("combo", "Combos (result & goals & BTTS …)", "Combo", "combo", False,
                          "35,36,37,547", "component models", "Combined selections."),
}

_BY_ID: Dict[str, str] = {}
for _k, _f in FAMILIES.items():
    for _i in _f.sportybet_ids.split(","):
        if _i.strip():
            _BY_ID[_i.strip()] = _k


def family_for(market_type: str) -> Optional[MarketFamily]:
    """Resolve a KAI market_type (or a SportyBet market id) to a family."""
    if not market_type:
        return None
    m = market_type.strip().lower()
    if m in FAMILIES:
        return FAMILIES[m]
    if m in _BY_ID:
        return FAMILIES[_BY_ID[m]]
    return None


def is_modelled(market_type: str) -> bool:
    f = family_for(market_type)
    return bool(f and f.modelled)


def modelled_keys() -> List[str]:
    return [k for k, f in FAMILIES.items() if f.modelled]


def reasoning_context(market_type: str) -> str:
    """Contextual text to append to a prediction's reasoning. Never invents options."""
    f = family_for(market_type)
    if not f:
        return (f"Market family unknown; SportyBet lists 273 market types for the "
                f"reference fixture — verify the exact market before staking.")
    base = f"{f.name} [{f.group}] — {f.hint}"
    if f.modelled:
        return base
    return (base + f". NOT yet modelled by KAI (needs {f.requires}); "
            "treated as unmodelled, not recommended.")
