import pytest

from core.media_factory import opportunities


def test_higher_trend_score_yields_higher_opportunity_score():
    low = opportunities.compute_opportunity(0.2, 0.2, 0.2, factory_key="public_domain_cartoon")
    high = opportunities.compute_opportunity(0.9, 0.9, 0.2, factory_key="public_domain_cartoon")
    assert high.score > low.score
    assert high.expected_payoff > low.expected_payoff


def test_competition_increases_effort_and_reduces_score():
    calm = opportunities.compute_opportunity(0.8, 0.8, 0.0, factory_key="public_domain_cartoon")
    crowded = opportunities.compute_opportunity(0.8, 0.8, 1.0, factory_key="public_domain_cartoon")
    assert crowded.effort > calm.effort
    assert crowded.score < calm.score


def test_effort_formula_is_explicit():
    opp = opportunities.compute_opportunity(1.0, 0.5, 0.5, factory_key="public_domain_cartoon")
    # base 1.0 + 0.5*0.5 + 0.5*(1-0.5) = 1.5
    assert opp.effort == pytest.approx(1.5)
    assert opp.expected_payoff == pytest.approx(1.0)
    assert opp.score == pytest.approx(1.0 / 1.5)
    assert "expected_payoff" in opp.formula


def test_factory_profiles_change_payoff():
    a = opportunities.compute_opportunity(1.0, 0.5, 0.5, factory_key="public_domain_cartoon")
    b = opportunities.compute_opportunity(1.0, 0.5, 0.5, factory_key="original_ai_drama")
    assert b.expected_payoff != a.expected_payoff
    assert b.effort != a.effort


def test_angle_includes_title():
    opp = opportunities.compute_opportunity(0.5, 0.5, 0.5, factory_key="original_ai_drama",
                                            title="a hot trend")
    assert "a hot trend" in opp.angle
