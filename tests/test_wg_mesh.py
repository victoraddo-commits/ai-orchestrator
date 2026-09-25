"""Tests for the A/B/C full-mesh WireGuard status (``core.wg_mesh``).

The live SSH transport is never exercised: ``wg_mesh.set_transport`` injects a
fake ``wg show`` dump, so no host is contacted. A separate route test mounts the
Command Center router on a bare FastAPI app.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import wg_mesh

NOW = int(time.time())
IDENT = {"X-Kai-User": "owner@kai", "X-Kai-User-Id": "owner"}
PRIV = "PRIVATEKEYMUSTNEVERAPPEAR"


def _dump(peers=(), pub="A" * 43 + "=", port=51900):
    lines = [f"{PRIV}\t{pub}\t{port}\t0"]
    for p in peers:
        lines.append("\t".join([
            p["pub"], "(none)", p.get("endpoint", ""),
            p.get("allowed", "10.10.10.0/24"), str(p.get("hs", 0)),
            str(p.get("rx", 0)), str(p.get("tx", 0)), "25",
        ]))
    return "\n".join(lines) + "\n"


def _peers(fresh=True):
    age = 0 if fresh else wg_mesh.HANDSHAKE_FRESH_S + 60
    return [
        {"pub": f"P{i}" + "B" * 42 + "=", "endpoint": f"100.0.0.{i}:51900",
         "allowed": f"10.10.10.{i}/32", "hs": NOW - age, "rx": 10, "tx": 20}
        for i in (1, 2)
    ]


@pytest.fixture(autouse=True)
def _reset_transport():
    yield
    wg_mesh.set_transport(None)


# ── command building ──────────────────────────────────────────────────────

def test_build_command_direct_for_host_b():
    node = next(n for n in wg_mesh.NODES if n["role"] == "B")
    cmd = wg_mesh.build_command(node)
    assert cmd[-2] == wg_mesh.PVE_B
    assert cmd[-1] == f"wg show {wg_mesh.IFACE} dump"


def test_build_command_jumps_through_pve_b_for_a():
    node = next(n for n in wg_mesh.NODES if n["role"] == "A")
    cmd = wg_mesh.build_command(node)
    assert cmd[-2] == wg_mesh.PVE_B
    assert wg_mesh.PVE_A in cmd[-1]
    assert f"wg show {wg_mesh.IFACE} dump" in cmd[-1]


# ── parsing ───────────────────────────────────────────────────────────────

def test_parse_dump_never_exposes_private_key():
    body = json.dumps(wg_mesh.parse_dump(_dump(_peers())))
    assert PRIV not in body
    assert "\\u2026" in body  # public keys are masked (JSON-escaped ellipsis)


def test_parse_dump_marks_fresh_peer_up():
    out = wg_mesh.parse_dump(_dump(_peers(fresh=True)))
    assert len(out["peers"]) == 2
    assert all(p["up"] for p in out["peers"])
    assert out["listen_port"] == 51900
    assert out["peers"][0]["keepalive"] == 25


def test_parse_dump_marks_stale_peer_down():
    out = wg_mesh.parse_dump(_dump(_peers(fresh=False)))
    assert not any(p["up"] for p in out["peers"])
    assert all(p["handshake_age_s"] > wg_mesh.HANDSHAKE_FRESH_S for p in out["peers"])


def test_parse_dump_empty_raises():
    with pytest.raises(wg_mesh.WgMeshError):
        wg_mesh.parse_dump("")


# ── mesh_status ───────────────────────────────────────────────────────────

def test_mesh_status_all_up():
    wg_mesh.set_transport(lambda node: _dump(_peers(fresh=True)))
    out = wg_mesh.mesh_status()
    assert out["mesh_ok"] is True
    assert out["expected_peers_per_node"] == 2
    assert len(out["nodes"]) == 3
    assert all(n["status"] == "up" and n["peer_count"] == 2 for n in out["nodes"])


def test_mesh_status_reports_unreachable_node():
    def transport(node):
        if node["role"] == "C":
            raise wg_mesh.WgMeshError("ssh failed: connection refused", 502)
        return _dump(_peers(fresh=True))

    wg_mesh.set_transport(transport)
    out = wg_mesh.mesh_status()
    assert out["mesh_ok"] is False
    c = next(n for n in out["nodes"] if n["role"] == "C")
    assert c["status"] == "unreachable" and c["error"]


def test_mesh_status_degraded_when_one_peer_missing():
    wg_mesh.set_transport(lambda node: _dump(_peers(fresh=True)[:1]))
    out = wg_mesh.mesh_status()
    assert out["mesh_ok"] is False
    assert all(n["status"] == "degraded" for n in out["nodes"])


# ── CC route ──────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from core import cc_extra_routes as cc
    app = FastAPI()
    app.include_router(cc.cc_extra_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_service_token(monkeypatch):
    monkeypatch.delenv("WG_CTL_TOKEN", raising=False)


def test_mesh_route_requires_operator(client):
    assert client.get("/api/wg/mesh").status_code == 401


def test_mesh_route_returns_snapshot(client, monkeypatch):
    monkeypatch.setattr(wg_mesh, "mesh_status",
                        lambda: {"schema": "wg-mesh/1", "mesh_ok": True, "nodes": []})
    r = client.get("/api/wg/mesh", headers=IDENT)
    assert r.status_code == 200
    assert r.json()["mesh_ok"] is True
