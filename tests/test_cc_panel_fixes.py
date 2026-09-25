"""Command Center panel fixes — 2026-09-25 batch.

Locks in the fixes for:
* Bug 1 — Kai Control Quick Actions must render structured data, never a raw
  ``<pre>`` JSON dump.
* Bug 2 — the CC HOME page must show a Data Usage card with a history graph
  sourced from ``/api/infra/usage`` + ``/api/infra/usage/history``.
* Bug 3 — the containers ("Docker") tab must list the real Proxmox B guests
  (CTs + VMs) instead of "No containers found".
* Server — the Docker client must use the async transport, and the Proxmox B
  host default must reflect the corrected topology (192.168.1.110).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC_HTML = ROOT / "core" / "kai" / "command_center.html"


def _html() -> str:
    return CC_HTML.read_text()


def _function_body(html: str, name: str) -> str:
    start = html.find("function " + name + "(")
    assert start != -1, f"{name} not found"
    tail = html[start:]
    end = re.search(r"\n(?:async )?function ", tail[10:])
    return tail[: end.start() + 10] if end else tail


# ---------------------------------------------------------------------------
# Bug 1 — Kai Control Quick Actions
# ---------------------------------------------------------------------------

def test_kai_ctl_renders_structured_not_raw_json():
    body = _function_body(_html(), "kaiCtl")
    assert "<pre" not in body, "kaiCtl still dumps a raw <pre> JSON block"
    assert "JSON.stringify" not in body, "kaiCtl still stringifies raw JSON"
    # It must delegate to a structured renderer instead.
    assert "ccStructured" in body or "kaiCtlRender" in body, (
        "kaiCtl must render the response through a structured renderer")


def test_cc_structured_renderer_exists():
    html = _html()
    assert ("function ccStructured(" in html
            or "function kaiCtlRender(" in html), (
        "a structured JSON->HTML renderer must exist for Quick Actions")


# ---------------------------------------------------------------------------
# Bug 2 — HOME data usage card + graph
# ---------------------------------------------------------------------------

def test_home_has_data_usage_card():
    html = _html()
    assert 'id="home-usage"' in html, "HOME page has no #home-usage card"
    assert "loadHomeUsage" in html, "HOME has no loadHomeUsage loader"
    home = _function_body(html, "loadHome")
    assert "loadHomeUsage" in home, "loadHome never calls loadHomeUsage"


def test_home_usage_uses_history_and_chart():
    body = _function_body(_html(), "loadHomeUsage")
    assert "/api/infra/usage" in body, "home usage must read /api/infra/usage"
    assert "/api/infra/usage/history?range=24h" in body, (
        "home usage must read the 24h history series")
    assert "nwChart" in body, "home usage must render a line/area chart"


# ---------------------------------------------------------------------------
# Bug 3 — containers tab lists real Proxmox guests
# ---------------------------------------------------------------------------

def test_docker_panel_lists_proxmox_guests():
    body = _function_body(_html(), "loadDocker")
    assert "/api/infra/usage" in body, (
        "the containers tab must source the real guests from /api/infra/usage")
    assert "loadProxmoxGuests" in body or "proxmox" in body.lower(), (
        "the containers tab must render Proxmox CTs + VMs")


def test_docker_client_uses_async_transport():
    import httpx
    from core.api import _get_docker_client

    client = _get_docker_client()
    assert isinstance(client, httpx.AsyncClient)
    transport = client._transport
    assert isinstance(transport, httpx.AsyncHTTPTransport), (
        "AsyncClient must use AsyncHTTPTransport for the docker UDS, not the "
        "sync HTTPTransport (which lacks handle_async_request)")


def test_proxmox_b_host_default_is_corrected_topology(monkeypatch):
    monkeypatch.delenv("PROXMOX_B_HOST", raising=False)
    from core.proxmox_monitor import _get_node_configs

    nodes = {n["name"]: n for n in _get_node_configs()}
    assert nodes["pve-b"]["host"] == "192.168.1.110", (
        "Proxmox B default host must be 192.168.1.110 (corrected topology); "
        "192.168.1.109 is unreachable")
