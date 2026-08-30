"""Tests for /network/* API routes (Task 7)."""

import pytest
from unittest.mock import patch


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    from fastapi.testclient import TestClient
    from core.api import app
    return TestClient(app)


@pytest.fixture
def sample_graph():
    """A minimal but realistic topology graph."""
    return {
        "schema_version": 1,
        "generated_at": "2026-08-30T12:00:00Z",
        "sites": {
            "SITE-A": {
                "name": "SITE-A",
                "lan_subnet": "192.168.99.0/24",
                "gateway": "192.168.99.254",
                "proxmox": {
                    "name": "pve",
                    "lan_ip": "192.168.99.2",
                    "tailscale_ip": "100.83.4.27",
                    "online": True,
                },
                "lxcs": [],
                "vms": [],
                "services": [],
            },
            "SITE-B": {
                "name": "SITE-B",
                "lan_subnet": "192.168.1.0/24",
                "gateway": "192.168.1.1",
                "proxmox": {
                    "name": "pve-b",
                    "lan_ip": "192.168.1.109",
                    "tailscale_ip": "100.89.97.76",
                    "online": True,
                },
                "lxcs": [],
                "vms": [],
                "services": [],
            },
        },
        "tailscale": {
            "peers": {
                "prox-a": {
                    "name": "prox-a",
                    "tailscale_ip": "100.83.4.27",
                    "online": True,
                    "last_seen": "2026-08-30T11:55:00Z",
                },
                "prox-b": {
                    "name": "prox-b",
                    "tailscale_ip": "100.89.97.76",
                    "online": True,
                    "last_seen": "2026-08-30T11:55:00Z",
                },
            },
            "subnet_routes": {
                "192.168.99.0/24": {"advertiser": "prox-a", "accepted": True},
                "192.168.1.0/24": {"advertiser": "prox-b", "accepted": True},
            },
        },
        "tunnel": {
            "status": "HEALTHY",
            "a_to_b_latency_ms": 12.3,
            "b_to_a_latency_ms": 11.8,
            "packet_loss_pct": 0.0,
            "last_test": "2026-08-30T11:58:00Z",
        },
        "connectivity": {
            "a_to_b_direct": "PASS",
            "b_to_a_direct": "PASS",
            "a_subnet_to_b_subnet": "PASS",
        },
        "last_discovery": "2026-08-30T11:58:00Z",
        "last_change": "2026-08-30T10:00:00Z",
    }


@pytest.fixture
def prior_graph():
    """A slightly different prior graph for change detection."""
    return {
        "schema_version": 1,
        "generated_at": "2026-08-30T11:00:00Z",
        "sites": {
            "SITE-A": {
                "name": "SITE-A",
                "lan_subnet": "192.168.99.0/24",
                "gateway": "192.168.99.254",
                "proxmox": {
                    "name": "pve",
                    "lan_ip": "192.168.99.2",
                    "tailscale_ip": "100.83.4.27",
                    "online": True,
                },
                "lxcs": [],
                "vms": [],
                "services": [],
            },
        },
        "tailscale": {
            "peers": {
                "prox-a": {
                    "name": "prox-a",
                    "tailscale_ip": "100.83.4.27",
                    "online": True,
                    "last_seen": "2026-08-30T10:55:00Z",
                },
            },
            "subnet_routes": {
                "192.168.99.0/24": {"advertiser": "prox-a", "accepted": True},
            },
        },
        "tunnel": {
            "status": "DEGRADED",
            "a_to_b_latency_ms": None,
            "b_to_a_latency_ms": None,
            "packet_loss_pct": None,
            "last_test": "2026-08-30T10:58:00Z",
        },
        "connectivity": {},
        "last_discovery": "2026-08-30T10:58:00Z",
        "last_change": None,
    }


# ── GET /network/topology ──────────────────────────────────────────────────────

def test_network_topology_returns_full_graph(client, sample_graph):
    with patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/topology")
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == 1
    assert "SITE-A" in data["sites"]
    assert "SITE-B" in data["sites"]
    assert "tailscale" in data
    assert "tunnel" in data


def test_network_topology_returns_empty_graph(client):
    empty = {
        "schema_version": 1,
        "generated_at": "2026-08-30T12:00:00Z",
        "sites": {},
        "tailscale": {"peers": {}, "subnet_routes": {}},
        "tunnel": {"status": "UNKNOWN", "a_to_b_latency_ms": None,
                   "b_to_a_latency_ms": None, "packet_loss_pct": None, "last_test": None},
        "connectivity": {},
        "last_discovery": None,
        "last_change": None,
    }
    with patch("core.api.load_graph", return_value=empty):
        response = client.get("/network/topology")
    assert response.status_code == 200
    assert response.json()["sites"] == {}


# ── GET /network/topology/sites ───────────────────────────────────────────────

def test_network_topology_sites_returns_sites(client, sample_graph):
    with patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/topology/sites")
    assert response.status_code == 200
    data = response.json()
    assert "sites" in data
    assert "SITE-A" in data["sites"]
    assert data["sites"]["SITE-A"]["lan_subnet"] == "192.168.99.0/24"


# ── GET /network/topology/peers ───────────────────────────────────────────────

def test_network_topology_peers_returns_peers(client, sample_graph):
    with patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/topology/peers")
    assert response.status_code == 200
    data = response.json()
    assert "peers" in data
    assert "prox-a" in data["peers"]
    assert data["peers"]["prox-a"]["online"] is True


def test_network_topology_peers_returns_empty(client, sample_graph):
    sample_graph["tailscale"]["peers"] = {}
    with patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/topology/peers")
    assert response.status_code == 200
    assert response.json()["peers"] == {}


# ── GET /network/topology/routes ──────────────────────────────────────────────

def test_network_topology_routes_returns_routes(client, sample_graph):
    with patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/topology/routes")
    assert response.status_code == 200
    data = response.json()
    assert "routes" in data
    assert "192.168.99.0/24" in data["routes"]
    assert data["routes"]["192.168.99.0/24"]["accepted"] is True


# ── GET /network/connectivity ─────────────────────────────────────────────────

def test_network_connectivity_returns_connectivity(client, sample_graph):
    with patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/connectivity")
    assert response.status_code == 200
    data = response.json()
    assert "connectivity" in data
    assert "tunnel" in data
    assert data["connectivity"]["a_to_b_direct"] == "PASS"
    assert data["tunnel"]["status"] == "HEALTHY"


# ── POST /network/connectivity/test ────────────────────────────────────────────

def test_network_connectivity_test_runs_discovery(client, sample_graph):
    with patch("core.api.run_network_discovery_cycle", return_value=sample_graph):
        response = client.post("/network/connectivity/test")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "connectivity" in data
    assert "tunnel" in data


# ── GET /network/changes ──────────────────────────────────────────────────────

def test_network_changes_returns_changes(client, sample_graph, prior_graph):
    with patch("core.api.load_prior", return_value=prior_graph), \
         patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/changes")
    assert response.status_code == 200
    data = response.json()
    assert "changes" in data
    assert data["total"] >= 0


def test_network_changes_returns_empty_when_no_prior(client, sample_graph):
    with patch("core.api.load_prior", return_value=None), \
         patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/changes")
    assert response.status_code == 200
    data = response.json()
    assert data["changes"] == []
    assert data["total"] == 0


def test_network_changes_respects_limit(client, sample_graph, prior_graph):
    with patch("core.api.load_prior", return_value=prior_graph), \
         patch("core.api.load_graph", return_value=sample_graph):
        response = client.get("/network/changes?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["changes"]) <= 5


# ── POST /network/discover ─────────────────────────────────────────────────────

def test_network_discover_runs_full_discovery(client, sample_graph):
    with patch("core.api.run_network_discovery_cycle", return_value=sample_graph):
        response = client.post("/network/discover")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "graph" in data
    assert "sites" in data["graph"]
