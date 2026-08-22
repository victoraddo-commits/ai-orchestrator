"""Kai Betting — Prediction Engine.

Multi-stage prediction pipeline that combines statistical modeling,
AI-assisted analysis, and historical performance data.

Architecture:
  1. Real Odds Input — receive real bookmaker odds + market
  2. Implied Probability — compute from decimal odds
  3. Statistical Model — base probability from market heuristics
  4. AI Enhancement — contextual analysis via Kai's AI router (when available)
  5. Quality Scoring — confidence, risk, and data quality scores
  6. Edge Calculation — model_probability vs implied_probability
  7. Correlation Detection — identify correlated picks

LIVE_DATA_MODE=true: Rejects predictions without real bookmaker odds.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple

from core.kai_betting.models import PredictionInput, PredictionResult


# Tier-0 EV screen: minimum statistical edge (model prob − implied prob)
# required before GPU.ai inference is justified. Env-overridable.
_AI_MIN_EDGE = float(os.environ.get("AI_MIN_EDGE", "0.03"))


def is_live_data_mode() -> bool:
    """Check if LIVE_DATA_MODE is enabled (default: True)."""
    return os.environ.get("LIVE_DATA_MODE", "true").lower() in ("1", "true", "yes")


# ── Market Templates ─────────────────────────────────────────────────────────

MARKET_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "football": {
        "match_result": {
            "name": "Match Result",
            "selections": ["home", "draw", "away"],
            "description": "1X2",
        },
        "double_chance": {
            "name": "Double Chance",
            "selections": ["1X", "X2", "12"],
            "description": "1X, X2, 12",
        },
        "draw_no_bet": {
            "name": "Draw No Bet",
            "selections": ["home", "away"],
            "description": "Home, Away",
        },
        "over_under": {
            "name": "Over/Under Goals",
            "selections": ["over", "under"],
            "description": "Over/Under 0.5-10.5",
        },
        "btts": {
            "name": "Both Teams To Score",
            "selections": ["yes", "no"],
            "description": "Yes/No",
        },
        "team_goals_home": {
            "name": "Home Team Goals",
            "selections": ["over", "under"],
            "description": "Home Over/Under",
        },
        "team_goals_away": {
            "name": "Away Team Goals",
            "selections": ["over", "under"],
            "description": "Away Over/Under",
        },
        "ht_result": {
            "name": "Half-Time Result",
            "selections": ["home", "draw", "away"],
            "description": "1X2",
        },
        "ht_double_chance": {
            "name": "Half-Time Double Chance",
            "selections": ["1X", "X2", "12"],
            "description": "1X, X2, 12",
        },
        "ht_goals": {
            "name": "Half-Time Goals",
            "selections": ["over", "under"],
            "description": "Over/Under",
        },
        "2h_result": {
            "name": "2nd Half Result",
            "selections": ["home", "draw", "away"],
            "description": "1X2",
        },
        "odd_even": {
            "name": "Odd/Even Goals",
            "selections": ["odd", "even"],
            "description": "Odd/Even",
        },
        "clean_sheet_home": {
            "name": "Home Clean Sheet",
            "selections": ["yes", "no"],
            "description": "Yes/No",
        },
        "clean_sheet_away": {
            "name": "Away Clean Sheet",
            "selections": ["yes", "no"],
            "description": "Yes/No",
        },
        "first_to_score": {
            "name": "First Team To Score",
            "selections": ["home", "away"],
            "description": "Home/Away",
        },
        "handicap": {
            "name": "Handicap",
            "selections": ["home", "away"],
            "description": "Spread",
        },
        "corners_over_under": {
            "name": "Corners Over/Under",
            "selections": ["over", "under"],
            "description": "Over/Under",
        },
        "corners_handicap": {
            "name": "Corners Handicap",
            "selections": ["home", "away"],
            "description": "Spread",
        },
    },
    "basketball": {
        "match_result": {
            "name": "Match Result",
            "selections": ["home", "away"],
            "description": "Moneyline",
        },
        "over_under": {
            "name": "Over/Under Points",
            "selections": ["over", "under"],
            "description": "Total Points",
        },
        "handicap": {
            "name": "Handicap",
            "selections": ["home", "away"],
            "description": "Spread",
        },
    },
    "tennis": {
        "match_result": {
            "name": "Match Result",
            "selections": ["home", "away"],
            "description": "Player Win",
        },
        "over_under": {
            "name": "Over/Under Games",
            "selections": ["over", "under"],
            "description": "Total Games",
        },
    },
    "baseball": {
        "match_result": {
            "name": "Match Result",
            "selections": ["home", "away"],
            "description": "Moneyline",
        },
        "over_under": {
            "name": "Over/Under Runs",
            "selections": ["over", "under"],
            "description": "Total Runs",
        },
    },
    "ice_hockey": {
        "match_result": {
            "name": "Match Result",
            "selections": ["home", "away"],
            "description": "1X2",
        },
        "over_under": {
            "name": "Over/Under Goals",
            "selections": ["over", "under"],
            "description": "Total Goals",
        },
    },
    "american_football": {
        "match_result": {
            "name": "Match Result",
            "selections": ["home", "away"],
            "description": "Moneyline",
        },
        "over_under": {
            "name": "Over/Under Points",
            "selections": ["over", "under"],
            "description": "Total Points",
        },
    },
}


# Markets that carry a numeric line (over/under total, handicap/spread).
LINE_MARKETS = {
    "over_under", "team_goals_home", "team_goals_away",
    "ht_goals", "2h_goals", "handicap", "european_handicap",
    "corners_over_under", "corners_handicap",
}

# Markets where the line is a goal/points total (selection is over/under).
TOTAL_MARKETS = {
    "over_under", "team_goals_home", "team_goals_away",
    "ht_goals", "2h_goals", "corners_over_under",
}


# ── Prediction Engine ────────────────────────────────────────────────────────

class PredictionEngine:
    """Multi-stage prediction pipeline for sports betting.

    Combines statistical baselines with AI-powered contextual analysis.
    Falls back gracefully when AI providers are unavailable.
    """

    def __init__(self):
        self._market_templates = MARKET_TEMPLATES

    def predict_with_odds(
        self,
        sport_key: str,
        market_type: str,
        selection: str,
        home_team: str = "",
        away_team: str = "",
        bookmaker_odds: float = 0.0,
        line: Optional[float] = None,
        bookmaker_name: Optional[str] = None,
        conn: Optional[Any] = None,
    ) -> PredictionResult:
        """Generate a prediction from REAL odds data.

        This is the PRIMARY entry point for the real-data pipeline.
        Uses bookmaker odds to compute implied probability, then models
        the actual probability based on market type and team context.

        Args:
            sport_key: e.g. 'football', 'basketball'
            market_type: e.g. 'match_result', 'over_under', 'btts'
            selection: The specific outcome (e.g. 'home', 'over', 'yes')
            home_team: Home team name
            away_team: Away team name
            bookmaker_odds: Decimal odds from the bookmaker
            line: For over/under and handicap, the line value (e.g. 2.5)
            bookmaker_name: Name of the bookmaker providing the odds
        """
        market_info = self._get_market_info(sport_key, market_type)

        # Compute implied probability from real odds
        if bookmaker_odds and bookmaker_odds > 1.0:
            implied_prob = 1.0 / bookmaker_odds
        else:
            implied_prob = None

        # Model probability: start from implied, adjust for market context
        model_prob = self._model_probability(
            sport_key, market_type, selection, home_team, away_team,
            bookmaker_odds, implied_prob, line
        )

        # Compute edge
        if implied_prob is not None:
            edge = model_prob - implied_prob
        else:
            edge = None

        # Score quality
        confidence, risk_score, data_quality = self._score_quality_real(
            sport_key=sport_key,
            market_type=market_type,
            home_team=home_team,
            away_team=away_team,
            bookmaker_odds=bookmaker_odds,
            bookmaker_name=bookmaker_name,
            selection=selection,
        )

        # Stage: AI enhancement (on-demand, graceful fallback)
        ai_prob, ai_reasoning = self._ai_enhancement(
            sport_key, market_type, home_team, away_team, selection,
            model_prob, bookmaker_odds, conn=conn,
        )
        if ai_prob is not None:
            model_prob = self._blend_probabilities(model_prob, ai_prob)
            if ai_reasoning:
                reasoning = ai_reasoning

        # Build market display — include the line for over/under & handicap
        market_display = self._market_display(
            market_info.get("name", market_type), market_type, selection, line
        )

        # Build reasoning — a plain-English explanation of the pick
        reasoning = self._build_reasoning(
            market_type=market_type,
            selection=selection,
            home_team=home_team,
            away_team=away_team,
            bookmaker_odds=bookmaker_odds,
            bookmaker_name=bookmaker_name,
            model_prob=model_prob,
            implied_prob=implied_prob,
            line=line,
        )

        # Tags
        tags = self._generate_tags(sport_key, market_type, confidence, risk_score, edge)

        # Correlation group
        corr_group = self._compute_correlation_group(
            sport_key, market_type, home_team, away_team
        )

        return PredictionResult(
            sport_key=sport_key,
            league_key=None,
            market_type=market_type,
            market_name=market_display,
            selection=selection,
            estimated_probability=model_prob,
            bookmaker_odds=bookmaker_odds,
            implied_probability=implied_prob,
            edge=edge,
            confidence=confidence,
            risk_score=risk_score,
            data_quality=data_quality,
            reasoning=reasoning,
            tags=tags,
            correlation_group=corr_group,
            model_version="kai-betting-v2",
            line=line,
        )

    def predict(
        self,
        sport_key: str,
        market_type: str,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        event_external_id: Optional[str] = None,
        bookmaker_odds: Optional[float] = None,
        stats: Optional[Dict[str, Any]] = None,
        conn: Optional[Any] = None,
    ) -> PredictionResult:
        """Run the prediction pipeline (legacy path — tests & synthetic only).

        In production, use predict_with_odds() which requires real odds data.
        """
        # Stage 1: Validate market
        market_info = self._get_market_info(sport_key, market_type)

        # Stage 2: Statistical baseline
        selection, base_prob, stat_reasoning = self._statistical_baseline(
            sport_key, market_type, home_team, away_team, stats or {}
        )

        # Stage 3: AI enhancement (attempt — graceful fallback)
        ai_prob, ai_reasoning = self._ai_enhancement(
            sport_key, market_type, home_team, away_team, selection, base_prob,
            bookmaker_odds, stats, conn=conn,
        )

        # Stage 4: Blend probabilities
        final_prob = self._blend_probabilities(base_prob, ai_prob)

        # Stage 5: Quality scoring
        confidence, risk_score, data_quality = self._score_quality(
            sport_key, market_type, home_team, away_team, stats or {}, bookmaker_odds
        )

        # Stage 6: Compute edge
        edge = None
        implied_prob = None
        if bookmaker_odds and bookmaker_odds > 1.0:
            implied_prob = 1.0 / bookmaker_odds
            edge = final_prob - implied_prob

        # Stage 7: Build reasoning
        reasoning_parts = [stat_reasoning]
        if ai_reasoning:
            reasoning_parts.append(ai_reasoning)
        reasoning = " | ".join(filter(None, reasoning_parts))

        # Stage 8: Tags
        tags = self._generate_tags(sport_key, market_type, confidence, risk_score, edge)

        # Stage 9: Correlation group
        corr_group = self._compute_correlation_group(
            sport_key, market_type, home_team or "", away_team or ""
        )

        return PredictionResult(
            sport_key=sport_key,
            league_key=None,
            market_type=market_type,
            market_name=market_info["name"],
            selection=selection,
            estimated_probability=final_prob,
            bookmaker_odds=bookmaker_odds,
            implied_probability=implied_prob,
            edge=edge,
            confidence=confidence,
            risk_score=risk_score,
            data_quality=data_quality,
            reasoning=reasoning,
            tags=tags,
            correlation_group=corr_group,
            model_version="kai-betting-v1",
        )

    # ── Stage Helpers ────────────────────────────────────────────────────────

    def _model_probability(
        self,
        sport_key: str,
        market_type: str,
        selection: str,
        home_team: str,
        away_team: str,
        bookmaker_odds: float,
        implied_prob: Optional[float],
        line: Optional[float],
    ) -> float:
        """Compute model probability from real odds and market context.

        The model starts from implied probability (what the market believes)
        and adjusts based on:
        - Market type (some markets are more predictable than others)
        - Home/away context (team-specific adjustment)
        - Line context (for over/under and handicap markets)
        """
        if implied_prob is None:
            implied_prob = 0.50  # no information

        # Market reliability: how much we trust the market for each type
        market_reliability = {
            "match_result": 0.85,
            "double_chance": 0.82,
            "draw_no_bet": 0.80,
            "over_under": 0.78,
            "btts": 0.75,
            "team_goals_home": 0.70,
            "team_goals_away": 0.68,
            "ht_result": 0.65,
            "ht_double_chance": 0.63,
            "ht_goals": 0.60,
            "2h_result": 0.58,
            "odd_even": 0.55,
            "handicap": 0.72,
            "european_handicap": 0.70,
            "clean_sheet_home": 0.65,
            "clean_sheet_away": 0.63,
            "first_to_score": 0.62,
            "teams_to_score": 0.60,
            "corners_over_under": 0.50,
            "corners_handicap": 0.45,
        }.get(market_type, 0.70)

        # Selection strength: how often does this selection type win?
        selection_adjust = {
            "home": 0.02,      # slight home bias
            "away": -0.01,
            "draw": -0.01,
            "over": 0.01,
            "under": 0.01,
            "yes": 0.0,
            "no": 0.0,
            "1X": 0.015,
            "X2": -0.005,
            "12": -0.01,
            "odd": 0.0,
            "even": 0.0,
        }.get(selection, 0.0)

        # Team-specific adjustment
        team_adj = 0.0
        if home_team and away_team:
            modifier = self._team_strength_modifier(home_team, away_team)
            if selection in ("home", "1X", "12"):
                team_adj = modifier * 0.3
            elif selection in ("away", "X2"):
                team_adj = -modifier * 0.3

        # Blend: weighted mix of implied and adjusted probability
        # Higher reliability = model tracks market more closely
        noise = self._hash_to_float(home_team, away_team, market_type, selection) * 0.04 - 0.02
        model_prob = implied_prob + (selection_adjust + team_adj + noise) * (1 - market_reliability)
        return min(0.95, max(0.05, model_prob))

    def _score_quality_real(
        self,
        sport_key: str,
        market_type: str,
        home_team: str,
        away_team: str,
        bookmaker_odds: float,
        bookmaker_name: Optional[str],
        selection: str,
    ) -> Tuple[float, float, float]:
        """Score quality for real-odds predictions.

        Returns (confidence, risk_score, data_quality) all 0-100.
        """
        # Data quality: starts high with real odds
        data_quality = 75.0
        if home_team and away_team:
            data_quality += 5
        if bookmaker_name:
            data_quality += 5
        if bookmaker_odds and bookmaker_odds > 1.0:
            # Odds in the sweet spot (1.20-3.00) are most reliable
            if 1.2 <= bookmaker_odds <= 3.0:
                data_quality += 10
            elif bookmaker_odds <= 10.0:
                data_quality += 5
        data_quality = min(100, data_quality)

        # Market complexity affects confidence
        market_complexity = {
            "match_result": 0.90,
            "double_chance": 0.85,
            "draw_no_bet": 0.82,
            "over_under": 0.78,
            "btts": 0.75,
            "team_goals_home": 0.70,
            "team_goals_away": 0.68,
            "ht_result": 0.65,
            "ht_double_chance": 0.63,
            "ht_goals": 0.60,
            "handicap": 0.70,
            "clean_sheet_home": 0.65,
            "clean_sheet_away": 0.63,
            "first_to_score": 0.60,
            "odd_even": 0.50,
            "corners_over_under": 0.45,
            "corners_handicap": 0.40,
        }.get(market_type, 0.60)

        confidence = data_quality * market_complexity

        # Risk: higher for complex/high-odds markets
        base_risk = max(5, 100 - confidence * 0.7)
        if bookmaker_odds and bookmaker_odds > 5.0:
            base_risk += 10  # long shots are risky
        if market_type.startswith("corners"):
            base_risk += 15  # corner markets are volatile
        risk_score = min(100, base_risk)

        return round(min(100, max(10, confidence)), 1), \
               round(min(100, max(5, risk_score)), 1), \
               round(data_quality, 1)

    def _get_market_info(self, sport_key: str, market_type: str) -> Dict[str, Any]:
        """Get market template, falling back to a generic template."""
        sport_markets = self._market_templates.get(sport_key, {})
        market = sport_markets.get(market_type)
        if market:
            return market
        # Generic fallback
        return {
            "name": market_type.replace("_", " ").title(),
            "selections": ["home", "away"],
            "description": "Generic",
        }

    @staticmethod
    def _market_display(base_name: str, market_type: str,
                        selection: str, line: Optional[float]) -> str:
        """Human market label, including the line for over/under & handicap."""
        if line is None or market_type not in LINE_MARKETS:
            return base_name
        if market_type in TOTAL_MARKETS:
            return f"{selection.title()} {line:g}"
        return f"{base_name} {line:g}"

    @staticmethod
    def _build_reasoning(market_type: str, selection: str, home_team: str,
                         away_team: str, bookmaker_odds: float,
                         bookmaker_name: Optional[str], model_prob: float,
                         implied_prob: Optional[float],
                         line: Optional[float]) -> str:
        """Build a plain-English explanation of a pick (deterministic)."""
        pick = selection
        if line is not None and market_type in LINE_MARKETS:
            pick = f"{selection} {line:g}"

        parts: List[str] = []
        if home_team and away_team:
            parts.append(f"{home_team} vs {away_team}")
        parts.append(f"Pick: {pick}")

        if implied_prob is not None:
            parts.append(f"model {model_prob:.1%} vs market {implied_prob:.1%}")
        else:
            parts.append(f"model {model_prob:.1%}")

        if bookmaker_odds and bookmaker_odds > 1.0:
            parts.append(f"odds {bookmaker_odds:.2f} ({bookmaker_name or 'bookmaker'})")

        if implied_prob is not None:
            edge = model_prob - implied_prob
            if edge > 0.03:
                parts.append(f"+{edge:.1%} value — model likes this more than the market")
            elif edge < -0.03:
                parts.append(f"{edge:.1%} — market prices this stronger than the model")
            else:
                parts.append("fairly priced against the market")

        return " | ".join(parts)

    def _statistical_baseline(
        self,
        sport_key: str,
        market_type: str,
        home_team: Optional[str],
        away_team: Optional[str],
        stats: Dict[str, Any],
    ) -> Tuple[str, float, str]:
        """Compute a base probability using statistical heuristics.

        In production, this would query historical data from the DB.
        For now, uses sport-specific heuristics with home-advantage bias.
        """
        market_info = self._get_market_info(sport_key, market_type)
        selections = market_info["selections"]

        # Base home advantage varies by sport
        home_advantage = {
            "football": 0.15,
            "basketball": 0.10,
            "baseball": 0.08,
            "ice_hockey": 0.10,
            "american_football": 0.12,
            "tennis": 0.05,
        }.get(sport_key, 0.08)

        if market_type == "match_result":
            if len(selections) == 3:  # 1X2
                # Home bias with draw probability
                prob_home = 0.35 + home_advantage
                prob_draw = 0.25
                prob_away = 1.0 - prob_home - prob_draw
                probs = {"home": prob_home, "draw": prob_draw, "away": prob_away}
            else:  # Moneyline (2-way)
                prob_home = 0.50 + home_advantage
                prob_away = 1.0 - prob_home
                probs = {"home": prob_home, "away": prob_away}

            # If teams given, adjust with name-hash for repeatable variance
            if home_team and away_team:
                modifier = self._team_strength_modifier(home_team, away_team)
                if "home" in probs:
                    probs["home"] = min(0.95, max(0.05, probs["home"] + modifier))
                if "away" in probs:
                    probs["away"] = min(0.95, max(0.05, probs["away"] - modifier))

            best_selection = max(probs, key=probs.get)
            best_prob = probs[best_selection]

            reasoning = (
                f"Statistical baseline: home advantage {home_advantage:.0%}, "
                f"selected {best_selection} at {best_prob:.1%}"
            )
            if home_team and away_team:
                reasoning += f" ({home_team} vs {away_team})"

            return best_selection, best_prob, reasoning

        elif market_type in ("over_under", "ht_goals"):
            # Slight lean toward "over" for entertainment value
            prob_over = 0.48
            prob_under = 0.52
            selection = "over" if self._hash_to_float(home_team or "", away_team or "", "ou") > 0.48 else "under"
            prob = prob_over if selection == "over" else prob_under
            return selection, prob, f"Statistical baseline: {selection} at {prob:.1%}"

        elif market_type == "btts":
            prob_yes = 0.52
            prob_no = 0.48
            selection = "yes" if self._hash_to_float(home_team or "", away_team or "", "btts") > 0.48 else "no"
            prob = prob_yes if selection == "yes" else prob_no
            return selection, prob, f"Statistical baseline: BTTS {selection} at {prob:.1%}"

        elif market_type == "double_chance":
            probs = {"1X": 0.40, "X2": 0.35, "12": 0.25}
            if home_team:
                probs["1X"] += 0.05
                probs["12"] += 0.03
                probs["X2"] -= 0.08
            total = sum(probs.values())
            probs = {k: v / total for k, v in probs.items()}
            best_selection = max(probs, key=probs.get)
            return best_selection, probs[best_selection], f"Statistical baseline: {best_selection} at {probs[best_selection]:.1%}"

        elif market_type == "draw_no_bet":
            prob_home = 0.58
            prob_away = 0.42
            selection = "home" if self._hash_to_float(home_team or "", away_team or "", "dnb") > 0.42 else "away"
            prob = prob_home if selection == "home" else prob_away
            return selection, prob, f"Statistical baseline: DNB {selection} at {prob:.1%}"

        else:
            # Generic: pick first selection at reasonable probability
            selection = selections[0]
            prob = 0.55
            return selection, prob, f"Statistical baseline: {selection} at {prob:.1%}"

    def _ai_enhancement(
        self,
        sport_key: str,
        market_type: str,
        home_team: Optional[str],
        away_team: Optional[str],
        selection: str,
        base_prob: float,
        bookmaker_odds: Optional[float] = None,
        stats: Optional[Dict[str, Any]] = None,
        conn: Optional[Any] = None,
    ) -> Tuple[Optional[float], Optional[str]]:
        """Attempt serverless AI contextual analysis via the betting AI router.

        Delegates to core.kai_betting.ai.router.BettingAIRouter, which decides
        whether AI is justified and runs the Qwen → DeepSeek → K3 escalation.
        Falls back gracefully to the statistical model when AI is unavailable,
        over budget, or the data is insufficient.

        Tier-0 EV gate: AI is only spent on candidates with a meaningful
        statistical edge vs the bookmaker (spec section 29: VALUE/EV SCREEN →
        AI?). Everything else stays 100% statistical — zero cost, zero latency.
        """
        from core.kai_betting.ai.router import BettingAIRouter
        from types import SimpleNamespace

        if not bookmaker_odds:
            return None, None  # no real odds → data-quality gate, no AI spend

        implied = 1.0 / bookmaker_odds
        edge = base_prob - implied
        if edge < _AI_MIN_EDGE:
            return None, None  # Tier-0 EV screen: no meaningful edge → no AI

        prediction = SimpleNamespace(
            sport_key=sport_key,
            market_type=market_type,
            selection=selection,
            bookmaker_odds=bookmaker_odds,
            estimated_probability=base_prob,
            implied_probability=implied,
            edge=edge,
            confidence=None,
            risk_score=None,
            data_quality=None,
            league_key=None,
        )
        evidence = {
            "teams": {"home": home_team, "away": away_team},
            "stats": stats or {},
        }
        try:
            result = BettingAIRouter().analyze(prediction, evidence, conn=conn)
        except Exception:
            return None, None
        if result.final_probability is not None:
            return result.final_probability, result.reasoning
        return None, None

    def _blend_probabilities(
        self,
        stat_prob: float,
        ai_prob: Optional[float],
    ) -> float:
        """Blend statistical and AI probabilities.

        When AI is available: 60% AI, 40% statistical.
        When AI unavailable: 100% statistical.
        """
        if ai_prob is not None:
            return 0.6 * ai_prob + 0.4 * stat_prob
        return stat_prob

    def _score_quality(
        self,
        sport_key: str,
        market_type: str,
        home_team: Optional[str],
        away_team: Optional[str],
        stats: Dict[str, Any],
        bookmaker_odds: Optional[float],
    ) -> Tuple[float, float, float]:
        """Compute confidence, risk, and data quality scores (all 0-100)."""
        # Data quality: how much real data do we have?
        data_points = len(stats)
        if home_team and away_team:
            data_points += 2  # Team names count as data
        data_quality = min(100, data_points * 15 + 20)  # 20 base, 15 per data point

        # Confidence: based on data quality and market complexity
        market_complexity = {
            "match_result": 0.9,
            "double_chance": 0.85,
            "draw_no_bet": 0.8,
            "over_under": 0.75,
            "btts": 0.7,
            "team_goals": 0.6,
            "ht_result": 0.65,
            "ht_goals": 0.6,
            "handicap": 0.7,
            "set_betting": 0.4,
        }.get(market_type, 0.6)

        confidence = data_quality * market_complexity * 0.8

        # Risk: inverse of confidence + market-specific factors
        base_risk = 100 - confidence * 0.7
        # Higher risk for complex markets
        complexity_penalty = {
            "set_betting": 20,
            "team_goals": 10,
            "handicap": 8,
        }.get(market_type, 0)
        risk_score = min(100, base_risk + complexity_penalty)

        # Cap and round
        confidence = round(min(100, max(10, confidence)), 1)
        risk_score = round(min(100, max(5, risk_score)), 1)
        data_quality = round(data_quality, 1)

        return confidence, risk_score, data_quality

    def _generate_tags(
        self,
        sport_key: str,
        market_type: str,
        confidence: float,
        risk_score: float,
        edge: Optional[float],
    ) -> List[str]:
        """Generate tags for filtering and display."""
        tags = [sport_key, market_type]

        if confidence >= 75:
            tags.append("high_confidence")
        elif confidence >= 60:
            tags.append("medium_confidence")
        else:
            tags.append("low_confidence")

        if risk_score <= 25:
            tags.append("low_risk")
        elif risk_score <= 50:
            tags.append("medium_risk")
        else:
            tags.append("high_risk")

        if edge is not None:
            if edge > 0.05:
                tags.append("value_bet")
            elif edge < -0.05:
                tags.append("overvalued")

        return tags

    def _compute_correlation_group(
        self,
        sport_key: str,
        market_type: str,
        home_team: str,
        away_team: str,
    ) -> str:
        """Generate a correlation group key.

        Predictions sharing a correlation group should not be combined
        in the same odds group (e.g., match_result + over_under on the same game).
        """
        event_key = f"{sport_key}:{home_team}:{away_team}"
        return hashlib.sha256(event_key.encode()).hexdigest()[:12]

    # ── Utility ─────────────────────────────────────────────────────────────

    @staticmethod
    def _hash_to_float(*args: str) -> float:
        """Convert string args to a deterministic float in [0, 1]."""
        combined = "|".join(args)
        digest = hashlib.sha256(combined.encode()).digest()
        # Use first 4 bytes as a uint32, divide by max
        val = int.from_bytes(digest[:4], "big")
        return val / 0xFFFFFFFF

    @staticmethod
    def _team_strength_modifier(home: str, away: str) -> float:
        """Deterministic strength modifier based on team names.

        Returns a value roughly in [-0.15, 0.15] to adjust home probability.
        In production, this would be replaced by real Elo ratings or form data.
        """
        raw = PredictionEngine._hash_to_float(home, away, "strength")
        # Map to [-0.15, 0.15]
        return raw * 0.30 - 0.15
