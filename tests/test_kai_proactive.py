"""P13 proactive engine tests — synthetic trends, no live data required."""

from core.kai_proactive import predict, _now_iso


def _trend(metric, cur, slope, r2=0.9, n=60):
    return {metric: {"current_value": cur, "slope_per_hour": slope,
                     "r_squared": r2, "sample_count": n}}


def test_disk_prediction_when_trending():
    preds = predict({"trends": _trend("host_disk_pct", 80, 0.5), "world": {}})
    disk = [p for p in preds if p["metric"] == "host_disk_pct"]
    assert len(disk) == 1
    p = disk[0]
    assert p["severity"] == "critical"          # ~40 days? 20/0.5=40h → >7d... check math below
    # (100-80)/0.5 = 40h ≈ 1.7 days → critical
    assert "day" in p["statement"]


def test_no_prediction_on_noise():
    preds = predict({"trends": _trend("host_disk_pct", 30, 0.4, r2=0.1)})
    assert [p for p in preds if p.get("metric")] == []   # low r² = noise
    preds2 = predict({"trends": _trend("host_disk_pct", 30, 0.0)})
    assert [p for p in preds2 if p.get("metric")] == []  # flat is flat


def test_memory_critical():
    preds = predict({"trends": _trend("host_memory_pct", 88, 0.3)})
    mem = [p for p in preds if p["metric"] == "host_memory_pct"]
    assert len(mem) == 1 and mem[0]["severity"] == "critical"


def test_world_detection_is_fact_not_prediction():
    preds = predict({"trends": {}, "world": {"critical": 2, "attention": 0}})
    d = [p for p in preds if p["kind"] == "detection"]
    assert len(d) == 1 and d[0]["confidence"] == 1.0


def test_healthy_system_is_quiet():
    preds = predict({"trends": _trend("host_disk_pct", 34.7, 0.01),
                     "world": {"critical": 0}})
    assert preds == []
