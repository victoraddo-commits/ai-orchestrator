"""Tests for Kai Betting prediction engine and odds engine."""

import pytest
from unittest.mock import patch, MagicMock
from core.kai_betting.prediction_engine import PredictionEngine, MARKET_TEMPLATES
from core.kai_betting.odds_engine import OddsEngine, ODDS_TARGETS, RISK_THRESHOLDS
from core.kai_betting.models import PredictionResult, OddsGroupResult


class TestPredictionEngineSportCoverage:
    """All 10 sports should be supported."""

    def test_all_sports_registered(self):
        engine = PredictionEngine()
        for sport in ["football", "basketball", "tennis", "baseball",
                      "ice_hockey", "american_football"]:
            info = engine._get_market_info(sport, "match_result")
            assert info is not None
            assert "selections" in info

    def test_unknown_sport_graceful(self):
        """Unknown sports get a generic fallback template."""
        engine = PredictionEngine()
        info = engine._get_market_info("unknown_sport", "match_result")
        assert info is not None
        assert "home" in info["selections"]

    def test_unknown_market_graceful(self):
        """Unknown markets get a generic fallback."""
        engine = PredictionEngine()
        info = engine._get_market_info("football", "unknown_market")
        assert info is not None
        assert len(info["selections"]) > 0


class TestPredictionEngineMatchResult:
    """Match result predictions for various sports."""

    def test_football_match_result(self):
        engine = PredictionEngine()
        result = engine.predict("football", "match_result", "Barcelona", "Real Madrid")
        assert result.selection in ("home", "draw", "away")
        assert 0 < result.estimated_probability < 1
        assert "Statistical baseline" in result.reasoning

    def test_football_home_advantage(self):
        """Home team should get probability boost."""
        engine = PredictionEngine()
        results = []
        for _ in range(20):
            r = engine.predict("football", "match_result", "HomeFC", "AwayFC")
            results.append(r.selection)
        # Home should be selected more often with home advantage
        assert results.count("home") > results.count("away")

    def test_deterministic_with_same_inputs(self):
        """Same inputs should produce same output (hash-based)."""
        engine = PredictionEngine()
        r1 = engine.predict("football", "match_result", "Chelsea", "Arsenal")
        r2 = engine.predict("football", "match_result", "Chelsea", "Arsenal")
        assert r1.selection == r2.selection
        assert r1.estimated_probability == r2.estimated_probability

    def test_different_teams_different_output(self):
        """Different teams should (usually) produce different output."""
        engine = PredictionEngine()
        r1 = engine.predict("football", "match_result", "Chelsea", "Arsenal")
        r2 = engine.predict("football", "match_result", "Barcelona", "Real Madrid")
        # Might be same selection by chance, but different probabilities due to hash modifier
        assert (r1.selection != r2.selection) or (
            r1.estimated_probability != r2.estimated_probability
        )

    def test_basketball_two_way(self):
        """Basketball is 2-way (no draw)."""
        engine = PredictionEngine()
        result = engine.predict("basketball", "match_result", "Lakers", "Celtics")
        assert result.selection in ("home", "away")
        assert result.selection != "draw"

    def test_tennis_match_result(self):
        engine = PredictionEngine()
        result = engine.predict("tennis", "match_result", "Djokovic", "Nadal")
        assert result.selection in ("home", "away")

    def test_baseball_match_result(self):
        engine = PredictionEngine()
        result = engine.predict("baseball", "match_result", "Yankees", "Red Sox")
        assert result.selection in ("home", "away")

    def test_ice_hockey_match_result(self):
        engine = PredictionEngine()
        result = engine.predict("ice_hockey", "match_result", "Bruins", "Canadiens")
        assert result.selection in ("home", "away")

    def test_american_football_match_result(self):
        engine = PredictionEngine()
        result = engine.predict("american_football", "match_result", "Chiefs", "49ers")
        assert result.selection in ("home", "away")


class TestPredictionEngineOverUnder:
    """Over/Under predictions."""

    def test_over_under_football(self):
        engine = PredictionEngine()
        result = engine.predict("football", "over_under", "Liverpool", "Man City")
        assert result.selection in ("over", "under")
        assert 0.4 < result.estimated_probability < 0.6

    def test_over_under_basketball(self):
        engine = PredictionEngine()
        result = engine.predict("basketball", "over_under")
        assert result.selection in ("over", "under")

    def test_over_under_tennis(self):
        engine = PredictionEngine()
        result = engine.predict("tennis", "over_under")
        assert result.selection in ("over", "under")


class TestPredictionEngineBtts:
    """Both Teams To Score predictions."""

    def test_btts(self):
        engine = PredictionEngine()
        result = engine.predict("football", "btts", "Newcastle", "Tottenham")
        assert result.selection in ("yes", "no")


class TestPredictionEngineDoubleChance:
    """Double chance predictions."""

    def test_double_chance(self):
        engine = PredictionEngine()
        result = engine.predict("football", "double_chance", "Bayern", "Dortmund")
        assert result.selection in ("1X", "X2", "12")
        assert result.estimated_probability > 0.3  # Double chance: any single selection in 3-outcome space


class TestPredictionEngineDrawNoBet:
    """Draw No Bet predictions."""

    def test_draw_no_bet(self):
        engine = PredictionEngine()
        result = engine.predict("football", "draw_no_bet", "PSG", "Marseille")
        assert result.selection in ("home", "away")


class TestPredictionEngineEdge:
    """Edge calculation with bookmaker odds."""

    def test_edge_calculation(self):
        engine = PredictionEngine()
        result = engine.predict("football", "match_result", "TeamA", "TeamB", bookmaker_odds=2.0)
        assert result.bookmaker_odds == 2.0
        assert result.implied_probability is not None
        assert result.edge is not None
        # implied = 1/2.0 = 0.5, edge = estimated - 0.5
        assert abs(result.edge - (result.estimated_probability - 0.5)) < 0.001

    def test_no_edge_without_odds(self):
        engine = PredictionEngine()
        result = engine.predict("football", "match_result", "TeamA", "TeamB")
        assert result.edge is None
        assert result.implied_probability is None

    def test_invalid_odds_handled(self):
        engine = PredictionEngine()
        # Negative or zero odds should be handled gracefully
        result = engine.predict("football", "match_result", "TeamA", "TeamB", bookmaker_odds=0)
        assert result.bookmaker_odds == 0
        # No division by zero


class TestPredictionEngineQualityScores:
    """Confidence, risk, and data quality scoring."""

    def test_all_scores_0_to_100(self):
        engine = PredictionEngine()
        result = engine.predict("football", "match_result", "TeamA", "TeamB")
        assert 0 <= result.confidence <= 100
        assert 0 <= result.risk_score <= 100
        assert 0 <= result.data_quality <= 100

    def test_confidence_increases_with_data(self):
        engine = PredictionEngine()
        no_data = engine.predict("football", "match_result")
        with_data = engine.predict("football", "match_result", "TeamA", "TeamB")
        # Having team names increases data quality → higher confidence
        assert with_data.data_quality >= no_data.data_quality

    def test_complex_markets_higher_risk(self):
        engine = PredictionEngine()
        simple = engine.predict("football", "match_result", "TeamA", "TeamB")
        complex_ = engine.predict("tennis", "set_betting", "PlayerA", "PlayerB")
        # Set betting has lower complexity factor (0.4) → lower confidence, higher risk
        assert complex_.confidence < simple.confidence
        assert complex_.risk_score > simple.risk_score


class TestPredictionEngineTags:
    """Tag generation."""

    def test_tags_include_sport_and_market(self):
        engine = PredictionEngine()
        result = engine.predict("football", "over_under", "TeamA", "TeamB")
        assert "football" in result.tags
        assert "over_under" in result.tags

    def test_value_bet_tag(self):
        """Positive edge should produce value_bet tag."""
        engine = PredictionEngine()
        # Use high odds to ensure positive edge
        result = engine.predict("football", "match_result", "TeamA", "TeamB", bookmaker_odds=10.0)
        if result.edge and result.edge > 0.05:
            assert "value_bet" in result.tags

    def test_confidence_tags(self):
        engine = PredictionEngine()
        result = engine.predict("football", "match_result", "TeamA", "TeamB")
        confidence_tags = [t for t in result.tags if "confidence" in t]
        assert len(confidence_tags) == 1


class TestPredictionEngineCorrelation:
    """Correlation group detection."""

    def test_correlation_group_same_event(self):
        engine = PredictionEngine()
        r1 = engine.predict("football", "match_result", "Chelsea", "Arsenal")
        r2 = engine.predict("football", "over_under", "Chelsea", "Arsenal")
        assert r1.correlation_group == r2.correlation_group  # Same event

    def test_correlation_group_different_event(self):
        engine = PredictionEngine()
        r1 = engine.predict("football", "match_result", "Chelsea", "Arsenal")
        r2 = engine.predict("football", "match_result", "Liverpool", "Man City")
        assert r1.correlation_group != r2.correlation_group


class TestPredictionEngineReasoning:
    """Reasoning output."""

    def test_reasoning_includes_sport(self):
        engine = PredictionEngine()
        result = engine.predict("football", "match_result", "TeamA", "TeamB")
        assert "Statistical baseline" in result.reasoning

    def test_reasoning_without_teams(self):
        engine = PredictionEngine()
        result = engine.predict("basketball", "over_under")
        assert len(result.reasoning) > 0


class TestOddsEngine:
    """Odds group generation."""

    def test_all_risk_levels_generate(self):
        import os
        os.environ["LIVE_DATA_MODE"] = "false"  # Allow synthetic for tests
        engine = PredictionEngine()
        odds = OddsEngine(engine)
        for risk in ["conservative", "moderate", "aggressive", "high_risk"]:
            group = odds.generate(target_odds=10, risk_level=risk, min_selections=2)
            assert len(group.selections) >= 2, f"Risk {risk} returned {len(group.selections)} selections (status: {group.status})"
            assert group.risk_level == risk
            assert group.combined_odds > 1.0

    def test_label_generation(self):
        import os
        os.environ["LIVE_DATA_MODE"] = "false"
        engine = PredictionEngine()
        odds = OddsEngine(engine)
        group = odds.generate(target_odds=10, risk_level="moderate", min_selections=2)
        assert "10" in group.label or group.label == "10 ODDS"

    def test_no_correlated_selections_in_group(self):
        """Odds group should not contain correlated selections."""
        engine = PredictionEngine()
        odds = OddsEngine(engine)
        group = odds.generate(target_odds=50, risk_level="moderate", min_selections=3, max_selections=8)
        seen_groups = set()
        for sel in group.selections:
            assert sel.correlation_group not in seen_groups, "Correlated selections in same group!"
            seen_groups.add(sel.correlation_group)

    def test_min_selections_respected(self):
        engine = PredictionEngine()
        odds = OddsEngine(engine)
        group = odds.generate(target_odds=10, risk_level="moderate", min_selections=4, max_selections=8)
        assert len(group.selections) >= 4

    def test_max_selections_respected(self):
        engine = PredictionEngine()
        odds = OddsEngine(engine)
        group = odds.generate(target_odds=5, risk_level="moderate", min_selections=1, max_selections=3)
        assert len(group.selections) <= 3

    def test_all_odds_targets_work(self):
        engine = PredictionEngine()
        odds = OddsEngine(engine)
        for target in [5, 10, 50, 100, 200]:
            group = odds.generate(target_odds=float(target), risk_level="moderate", min_selections=2)
            assert group.target_odds == float(target)

    def test_available_sizes(self):
        engine = PredictionEngine()
        odds = OddsEngine(engine)
        sizes = odds.list_available_sizes()
        assert len(sizes) >= 4
        for s in sizes:
            assert "target_odds" in s
            assert "label" in s
            assert "risk_level" in s


class TestPredictionResultDataclass:
    """PredictionResult dataclass behavior."""

    def test_auto_computed_fields(self):
        result = PredictionResult(
            sport_key="football",
            league_key=None,
            market_type="match_result",
            market_name="Match Result",
            selection="home",
            estimated_probability=0.55,
            bookmaker_odds=2.0,
        )
        assert result.implied_probability == 0.5
        assert result.edge == pytest.approx(0.05)

    def test_no_auto_compute_without_odds(self):
        result = PredictionResult(
            sport_key="football",
            league_key=None,
            market_type="match_result",
            market_name="Match Result",
            selection="home",
            estimated_probability=0.55,
        )
        assert result.implied_probability is None
        assert result.edge is None


class TestOddsGroupResultDataclass:
    """OddsGroupResult dataclass behavior."""

    def test_auto_computed_combined_odds(self):
        selections = [
            PredictionResult(
                sport_key="football", league_key=None,
                market_type="match_result", market_name="MR",
                selection="home", estimated_probability=0.55, bookmaker_odds=1.5,
            ),
            PredictionResult(
                sport_key="football", league_key=None,
                market_type="match_result", market_name="MR",
                selection="home", estimated_probability=0.5, bookmaker_odds=2.0,
            ),
        ]
        group = OddsGroupResult(
            target_odds=3,
            label="3 ODDS",
            risk_level="moderate",
            selections=selections,
        )
        assert group.combined_odds == 3.0  # 1.5 * 2.0
