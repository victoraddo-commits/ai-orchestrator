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


from services.kai_directory.discovery import (
    parse_docker, parse_cloudflared, parse_panels, discover_all,
)


def test_parse_docker_ps():
    out = (
        "kai-money-money-center-1\t0.0.0.0:8095->8095/tcp\tkai-money-money-center\n"
        "kai-money-db-1\t5432/tcp\tpostgres:16-alpine\n"
    )
    recs = parse_docker(out, ip="192.168.1.118", host="ct108")
    assert len(recs) == 1                      # db has no published port
    assert recs[0].port == 8095
    assert recs[0].source == "docker"
    assert "money-center" in recs[0].name


def test_parse_cloudflared_ingress():
    yml = """
ingress:
  - hostname: command.deerude.com
    service: http://localhost:80
  - hostname: careers.deerude.com
    service: http://192.168.1.115:4000
  - service: http_status:404
"""
    recs = parse_cloudflared(yml)
    assert {r.display_name for r in recs} == {"command.deerude.com", "careers.deerude.com"}
    assert all(r.source == "cloudflared" for r in recs)


def test_parse_panels():
    html = '<a data-hash="#money">Money</a><a data-hash="#legal">Legal</a><a href="x">no</a>'
    recs = parse_panels(html)
    assert {r.name for r in recs} == {"money", "legal"}
    assert all(r.source == "panel" for r in recs)


def test_discover_all_never_raises(monkeypatch):
    monkeypatch.setattr("services.kai_directory.discovery._run", lambda *a, **k: "")
    monkeypatch.setattr("services.kai_directory.discovery._read_json", lambda *a, **k: None)
    monkeypatch.setattr("services.kai_directory.discovery._read_text", lambda *a, **k: "")
    assert discover_all() == []
