from services.kai_directory.discovery import (
    parse_registry, parse_ss, LOOPBACK_BIND,
)


def test_parse_registry_json():
    doc = {"services": [
        {"name": "kai-legal-brain", "host": "192.168.1.100", "port": 8100, "category": "legal"},
        {"name": "susu", "host": "192.168.1.111", "port": 8050, "category": "finances"},
    ]}
    recs = parse_registry(doc, host_label="ct")
    assert {r.name for r in recs} == {"kai-legal-brain", "susu"}
    assert recs[0].source == "registry"
    assert recs[0].port == 8100


def test_parse_ss_filters_loopback_and_keeps_wildcard():
    ss_output = (
        "LISTEN 0 2048 0.0.0.0:8099 0.0.0.0:* users:((\"uvicorn\",pid=1,fd=6))\n"
        "LISTEN 0 128 127.0.0.1:5432 0.0.0.0:* users:((\"postgres\",pid=2,fd=5))\n"
        "LISTEN 0 128 [::]:4000 [::]:*  users:((\"python3\",pid=3,fd=4))\n"
    )
    recs = parse_ss(ss_output, ip="192.168.1.111", host="ct111")
    ports = sorted(r.port for r in recs)
    assert ports == [4000, 8099]          # 5432 loopback dropped
    assert all(r.source == "ss" for r in recs)
    assert recs[0].bind in ("0.0.0.0", "::")


def test_loopback_bind_constant():
    assert "127.0.0.1" in LOOPBACK_BIND
