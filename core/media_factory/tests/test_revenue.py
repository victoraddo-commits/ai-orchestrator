from core.media_factory import revenue


def test_no_data_is_unverified_zero():
    result = revenue.compute_profitability([], [])
    assert result["status"] == "UNVERIFIED"
    assert result["profit"] == 0
    assert result["has_data"] is False


def test_verified_revenue_minus_cost_is_profit():
    revenues = [{"amount": 100, "verified": True}, {"amount": 50, "verified": True}]
    costs = [{"amount": 30}]
    result = revenue.compute_profitability(revenues, costs)
    assert result["revenue_verified"] == 150
    assert result["cost_total"] == 30
    assert result["profit"] == 120
    assert result["status"] == "VERIFIED"


def test_unverified_revenue_excluded_from_profit_but_reported():
    revenues = [{"amount": 100, "verified": True}, {"amount": 40, "verified": False}]
    costs = [{"amount": 10}]
    result = revenue.compute_profitability(revenues, costs)
    assert result["revenue_total"] == 140
    assert result["revenue_verified"] == 100
    assert result["revenue_unverified"] == 40
    assert result["profit"] == 90
    assert result["status"] == "PARTIALLY_VERIFIED"


def test_attribution_without_data_is_honest(monkeypatch):
    monkeypatch.setattr(revenue.db, "query", lambda *a, **k: [])
    result = revenue.attribution()
    assert result["status"] == "UNVERIFIED"
    assert result["attributions"] == []
