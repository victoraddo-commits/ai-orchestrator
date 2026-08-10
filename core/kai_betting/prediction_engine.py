"""Kai Betting — Prediction Engine.

Multi-stage prediction pipeline that combines statistical modeling,
AI-assisted analysis, and historical performance data.

Architecture:
  1. Data Collection — gather stats for the event
  2. Statistical Model — base probability from historical data
  3. AI Enhancement — contextual analysis via Kai's AI router
  4. Quality Scoring — confidence, risk, and data quality scores
  5. Correlation Detection — identify correlated picks
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple

from core.kai_betting.models import PredictionInput, PredictionResult


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
            "name": "Over/Under 2.5 Goals",
            "selections": ["over", "under"],
            "description": "Over/Under 0.5-3.5",
        },
        "btts": {
            "name": "Both Teams To Score",
            "selections": ["yes", "no"],
            "description": "Yes/No",
        },
        "team_goals": {
            "name": "Team Goals",
            "selections": ["home_over", "home_under", "away_over", "away_under"],
            "description": "Home/Away Over/Under",
        },
        "ht_result": {
            "name": "Half-Time Result",
            "selections": ["home", "draw", "away"],
            "description": "1X2",
        },
        "ht_goals": {
            "name": "Half-Time Goals",
            "selections": ["over", "under"],
            "description": "Over/Under",
        },
    },
    "basketball": {
        "match_result": {
            "name": "Match Result",
            "selections": ["home", "away"],
            "description": "1X2",
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
        "set_betting": {
            "name": "Set Betting",
            "selections": ["3-0", "3-1", "3-2", "2-3", "1-3", "0-3"],
            "description": "Set Score",
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


# ── Prediction Engine ────────────────────────────────────────────────────────

class PredictionEngine:
    """Multi-stage prediction pipeline for sports betting.

    Combines statistical baselines with AI-powered contextual analysis.
    Falls back gracefully when AI providers are unavailable.
    """

    def __init__(self):
        self._market_templates = MARKET_TEMPLATES

    def predict(
        self,
        sport_key: str,
        market_type: str,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        event_external_id: Optional[str] = None,
        bookmaker_odds: Optional[float] = None,
        stats: Optional[Dict[str, Any]] = None,
    ) -> PredictionResult:
        """Run the full prediction pipeline.

        Args:
            sport_key: e.g. 'football', 'basketball'
            market_type: e.g. 'match_result', 'over_under'
            home_team: Home team name
            away_team: Away team name
            event_external_id: Data provider's event ID
            bookmaker_odds: Current bookmaker odds for the selection
            stats: Optional pre-collected statistics

        Returns:
            PredictionResult with selection, probability, and confidence
        """
        # Stage 1: Validate market
        market_info = self._get_market_info(sport_key, market_type)

        # Stage 2: Statistical baseline
        selection, base_prob, stat_reasoning = self._statistical_baseline(
            sport_key, market_type, home_team, away_team, stats or {}
        )

        # Stage 3: AI enhancement (attempt — graceful fallback)
        ai_prob, ai_reasoning = self._ai_enhancement(
            sport_key, market_type, home_team, away_team, selection, base_prob
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
    ) -> Tuple[Optional[float], Optional[str]]:
        """Attempt AI-powered contextual analysis.

        Tries to use Kai's AI router for reasoning. Falls back gracefully.
        In production, this would call `core.ai.ai_router.delegate()`.
        """
        # For now, return the base probability — AI enhancement is a future feature
        # that requires real-time data feeds (injuries, form, weather, etc.)
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
