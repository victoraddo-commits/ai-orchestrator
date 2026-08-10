"""Kai Betting — Odds Group Engine.

Builds accumulator/parlay odds groups from individual predictions.
Handles correlation detection, risk-level filtering, and odds target matching.
"""

from __future__ import annotations

import math
from typing import Optional, List, Dict, Any

from core.kai_betting.models import (
    PredictionResult, OddsGroupResult, RiskLevel,
)
from core.kai_betting.prediction_engine import PredictionEngine
from core.kai_betting.db import get_db


# ── Odds Group Targets ───────────────────────────────────────────────────────

ODDS_TARGETS = [
    {"odds": 5, "label": "5 ODDS", "risk_level": "conservative"},
    {"odds": 10, "label": "10 ODDS", "risk_level": "conservative"},
    {"odds": 50, "label": "50 ODDS", "risk_level": "moderate"},
    {"odds": 100, "label": "100 ODDS", "risk_level": "moderate"},
    {"odds": 200, "label": "200 ODDS", "risk_level": "aggressive"},
    {"odds": 500, "label": "500 ODDS", "risk_level": "aggressive"},
    {"odds": 1000, "label": "1000 ODDS", "risk_level": "high_risk"},
]

RISK_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "conservative": {
        "min_confidence": 60,
        "max_risk_per_selection": 35,
        "preferred_markets": ["match_result", "double_chance", "draw_no_bet"],
        "max_selections": 4,
    },
    "moderate": {
        "min_confidence": 50,
        "max_risk_per_selection": 50,
        "preferred_markets": ["match_result", "double_chance", "over_under", "btts"],
        "max_selections": 8,
    },
    "aggressive": {
        "min_confidence": 40,
        "max_risk_per_selection": 65,
        "preferred_markets": ["match_result", "over_under", "btts", "team_goals", "handicap"],
        "max_selections": 15,
    },
    "high_risk": {
        "min_confidence": 20,
        "max_risk_per_selection": 100,
        "preferred_markets": [],  # all markets
        "max_selections": 50,
    },
}


class OddsEngine:
    """Generates odds groups (accumulators) from predictions.

    Combines individual predictions into multi-selection accumulators
    that target specific odds levels while respecting risk parameters
    and correlation constraints.
    """

    def __init__(self, prediction_engine: Optional[PredictionEngine] = None):
        self._prediction_engine = prediction_engine or PredictionEngine()

    def generate(
        self,
        target_odds: float,
        risk_level: str = "moderate",
        sport_keys: Optional[List[str]] = None,
        market_types: Optional[List[str]] = None,
        min_selections: int = 2,
        max_selections: int = 8,
        existing_predictions: Optional[List[int]] = None,
    ) -> OddsGroupResult:
        """Generate an odds group targeting specific combined odds.

        Args:
            target_odds: Target combined odds (e.g. 5, 10, 50, 100)
            risk_level: One of 'conservative', 'moderate', 'aggressive', 'high_risk'
            sport_keys: Optional list of sport keys to include
            market_types: Optional list of market types to include
            min_selections: Minimum number of selections in the group
            max_selections: Maximum number of selections in the group
            existing_predictions: Optional pre-existing prediction IDs from DB

        Returns:
            OddsGroupResult with combined selections
        """
        risk_config = RISK_THRESHOLDS.get(risk_level, RISK_THRESHOLDS["moderate"])

        # Determine target label
        label = f"{target_odds:.0f} ODDS"
        for target in ODDS_TARGETS:
            if target["odds"] == target_odds:
                label = target["label"]
                break

        # Get candidate predictions
        candidates = self._get_candidates(
            risk_config=risk_config,
            sport_keys=sport_keys,
            market_types=market_types,
            existing_predictions=existing_predictions,
        )

        if not candidates:
            # Generate synthetic candidates for now
            candidates = self._generate_synthetic_candidates(
                risk_config=risk_config,
                sport_keys=sport_keys,
                market_types=market_types,
            )

        # Build the odds group using greedy selection
        selections = self._build_group(
            candidates=candidates,
            target_odds=target_odds,
            risk_config=risk_config,
            min_selections=min_selections,
            max_selections=max_selections,
        )

        result = OddsGroupResult(
            target_odds=target_odds,
            label=label,
            risk_level=risk_level,
            selections=selections,
        )

        return result

    def _get_candidates(
        self,
        risk_config: Dict[str, Any],
        sport_keys: Optional[List[str]],
        market_types: Optional[List[str]],
        existing_predictions: Optional[List[int]],
    ) -> List[PredictionResult]:
        """Get candidate predictions from the database."""
        candidates: List[PredictionResult] = []

        with get_db() as db:
            query = """
                SELECT p.*, s.key as sport_key
                FROM predictions p
                JOIN sports s ON s.id = p.sport_id
                WHERE p.status IN ('pending', 'approved', 'published')
                  AND p.confidence >= ?
                  AND p.risk_score <= ?
            """
            params: list = [
                risk_config["min_confidence"],
                risk_config["max_risk_per_selection"],
            ]

            if sport_keys:
                placeholders = ",".join(["?"] * len(sport_keys))
                query += f" AND s.key IN ({placeholders})"
                params.extend(sport_keys)

            if market_types:
                placeholders = ",".join(["?"] * len(market_types))
                query += f" AND p.market_type IN ({placeholders})"
                params.extend(market_types)

            if existing_predictions:
                placeholders = ",".join(["?"] * len(existing_predictions))
                query += f" AND p.id IN ({placeholders})"
                params.extend(existing_predictions)

            query += " ORDER BY p.confidence DESC, p.risk_score ASC LIMIT 100"

            rows = db.execute(query, params).fetchall()

            for row in rows:
                candidates.append(PredictionResult(
                    sport_key=row["sport_key"],
                    league_key=None,
                    market_type=row["market_type"],
                    market_name=row["market_name"],
                    selection=row["selection"],
                    estimated_probability=row["estimated_probability"],
                    bookmaker_odds=row["bookmaker_odds"],
                    implied_probability=row["implied_probability"],
                    edge=row["edge"],
                    confidence=row["confidence"],
                    risk_score=row["risk_score"],
                    data_quality=row["data_quality"],
                    reasoning=row["reasoning"] or "",
                    tags=row["tags"].split(",") if row["tags"] else [],
                    correlation_group=row["correlation_group"] or "",
                    model_version=row["model_version"] or "1.0.0",
                ))

        return candidates

    def _generate_synthetic_candidates(
        self,
        risk_config: Dict[str, Any],
        sport_keys: Optional[List[str]],
        market_types: Optional[List[str]],
    ) -> List[PredictionResult]:
        """Generate synthetic candidates when no DB predictions exist.

        This is a fallback for development/testing. In production,
        predictions come from the database after being generated by the
        prediction engine against real events.
        """
        all_sports = sport_keys or ["football", "basketball", "tennis"]
        all_markets = market_types or risk_config.get("preferred_markets") or ["match_result", "over_under"]

        candidates = []
        # Generate diverse candidates across sports
        for i, sport in enumerate(all_sports * 3):  # 3 candidates per sport
            market = all_markets[i % len(all_markets)]
            try:
                result = self._prediction_engine.predict(
                    sport_key=sport,
                    market_type=market,
                    home_team=f"Team_{i}_H",
                    away_team=f"Team_{i}_A",
                    bookmaker_odds=1.3 + (i * 0.15),  # Varying odds
                )
                # For synthetic data, include all — filtering happens at _build_group
                candidates.append(result)
            except Exception:
                continue

            if len(candidates) >= 30:
                break

        # Sort by confidence desc, risk asc
        candidates.sort(key=lambda r: (r.confidence, -r.risk_score), reverse=True)
        return candidates

    def _build_group(
        self,
        candidates: List[PredictionResult],
        target_odds: float,
        risk_config: Dict[str, Any],
        min_selections: int,
        max_selections: int,
    ) -> List[PredictionResult]:
        """Build an odds group using greedy selection with correlation avoidance.

        Strategy:
        1. Filter candidates by risk config
        2. Sort by confidence (desc) for quality
        3. Greedily add selections, skipping correlated events
        4. Stop when combined odds reach target or max selections hit
        """
        selected: List[PredictionResult] = []
        used_correlation_groups: set = set()
        current_odds = 1.0

        # Sort: prefer higher confidence, lower risk
        sorted_candidates = sorted(
            candidates,
            key=lambda r: (r.confidence, -r.risk_score),
            reverse=True,
        )

        for candidate in sorted_candidates:
            if len(selected) >= max_selections:
                break

            # Skip if we've already used this correlation group
            if candidate.correlation_group and candidate.correlation_group in used_correlation_groups:
                continue

            # Need bookmaker odds to compute combined odds
            if not candidate.bookmaker_odds or candidate.bookmaker_odds <= 1.0:
                candidate.bookmaker_odds = 1.0 / candidate.estimated_probability

            odds = candidate.bookmaker_odds

            # Check if adding this would overshoot target too much
            # Allow up to 20% overshoot for conservative, 50% for aggressive
            tolerance = 1.2 if risk_config["min_confidence"] >= 60 else 1.5
            if selected and current_odds * odds > target_odds * tolerance:
                continue

            selected.append(candidate)
            current_odds *= odds

            if candidate.correlation_group:
                used_correlation_groups.add(candidate.correlation_group)

            # Stop if we've reached the target and have enough selections
            if current_odds >= target_odds and len(selected) >= min_selections:
                break

        # Ensure minimum selections — fill with highest-confidence remaining if needed
        if len(selected) < min_selections:
            for candidate in sorted_candidates:
                if len(selected) >= min_selections:
                    break
                if candidate in selected:
                    continue
                if candidate.correlation_group and candidate.correlation_group in used_correlation_groups:
                    continue
                selected.append(candidate)
                if candidate.correlation_group:
                    used_correlation_groups.add(candidate.correlation_group)

        return selected

    def list_available_sizes(self) -> List[Dict[str, Any]]:
        """List available odds group sizes with risk descriptions."""
        return [
            {
                "target_odds": t["odds"],
                "label": t["label"],
                "risk_level": t["risk_level"],
                "typical_selections": self._estimate_selections(t["odds"]),
                "description": self._describe_target(t["odds"], t["risk_level"]),
            }
            for t in ODDS_TARGETS
        ]

    @staticmethod
    def _estimate_selections(target_odds: float) -> str:
        """Estimate typical number of selections for a target odds."""
        avg_odds_per_selection = 1.5  # typical minimum odds
        n = math.ceil(math.log(target_odds) / math.log(avg_odds_per_selection))
        if n <= 2:
            return "1-2"
        elif n <= 5:
            return f"2-{n}"
        else:
            return f"3-{n}"

    @staticmethod
    def _describe_target(odds: float, risk: str) -> str:
        """Human-readable description of an odds target."""
        if odds <= 5:
            return f"Low odds, {risk} — small accumulators with high hit rate"
        elif odds <= 50:
            return f"Mid odds, {risk} — balanced accumulators"
        elif odds <= 200:
            return f"High odds, {risk} — larger accumulators for bigger returns"
        else:
            return f"Very high odds, {risk} — speculative accumulators"
