"""Tests for genuine-change event publishing from the infra workers.

health_worker, network_discovery_cycle and provider_health_monitor had no
``kai_event_bus.publish`` calls, so the event bus never saw infra state
changes. These tests pin the three publishers and their dedupe behaviour.
"""

import pytest


def _capture(monkeypatch):
    import core.kai_event_bus as bus
    published = []

    def fake_publish(topic, payload, source="unknown", severity="informational", journal=None, replay=False):
        published.append({"topic": topic, "payload": payload, "source": source, "severity": severity})
        return 0

    monkeypatch.setattr(bus, "publish", fake_publish)
    return published


def test_network_cycle_publishes_node_changes(monkeypatch):
    import core.network_discovery_cycle as ndc

    published = _capture(monkeypatch)
    monkeypatch.setattr(ndc, "discover_tailscale", lambda: {})
    monkeypatch.setattr(ndc, "discover_proxmox", lambda: {})
    monkeypatch.setattr(ndc, "build_graph", lambda ts, px: {"nodes": []})
    monkeypatch.setattr(ndc, "test_site_paths", lambda a, b: {})
    monkeypatch.setattr(ndc, "load_graph", lambda: {"nodes": [], "seen": True})
    monkeypatch.setattr(ndc, "detect_changes", lambda prior, g: [
        {"type": "PEER_OFFLINE", "node": "pve-b"},
    ])
    monkeypatch.setattr(ndc, "save", lambda g: None)
    monkeypatch.setattr(ndc, "_emit_alert", lambda c: None)

    ndc.run_network_discovery_cycle()

    topics = [p["topic"] for p in published]
    assert "network.node.changed" in topics
    change = next(p for p in published if p["topic"] == "network.node.changed")
    assert change["payload"]["node"] == "pve-b"
    assert change["source"] == "network_discovery"


def test_network_cycle_no_changes_no_publish(monkeypatch):
    import core.network_discovery_cycle as ndc

    published = _capture(monkeypatch)
    monkeypatch.setattr(ndc, "discover_tailscale", lambda: {})
    monkeypatch.setattr(ndc, "discover_proxmox", lambda: {})
    monkeypatch.setattr(ndc, "build_graph", lambda ts, px: {"nodes": []})
    monkeypatch.setattr(ndc, "test_site_paths", lambda a, b: {})
    monkeypatch.setattr(ndc, "load_graph", lambda: {"nodes": [], "seen": True})
    monkeypatch.setattr(ndc, "detect_changes", lambda prior, g: [])
    monkeypatch.setattr(ndc, "save", lambda g: None)

    ndc.run_network_discovery_cycle()
    assert [p for p in published if p["topic"] == "network.node.changed"] == []


def _monitor(monkeypatch):
    import core.provider_health_monitor as phm

    monitor = phm.ProviderHealthMonitor()
    monkeypatch.setattr(phm.ai_provider, "list_providers", lambda: {
        "p1": {"kind": "cloud", "available": True, "enabled": True},
    })
    monkeypatch.setattr(phm.provider_health, "prune_stale_snapshots", lambda keys: None)
    monkeypatch.setattr(monitor, "_check_provider",
                        lambda name, info: {"health": "error", "checked_at": "t", "reason": "boom"})
    monkeypatch.setattr(monitor, "_store_health", lambda name, status: None)
    monkeypatch.setattr(monitor, "_notify_failure", lambda name, status: None)
    monkeypatch.setattr(monitor, "_write_state_file", lambda: None)
    return phm, monitor


def test_provider_monitor_publishes_only_on_change(monkeypatch):
    phm, monitor = _monitor(monkeypatch)
    published = _capture(monkeypatch)

    monitor._last_health = {"p1": "ok"}
    monitor.check_all_providers()
    changes = [p for p in published if p["topic"] == "provider.health.changed"]
    assert len(changes) == 1
    assert changes[0]["payload"] == {"provider": "p1", "from": "ok", "to": "error", "detail": "boom"}

    # Same health again -> deduped, no second publish.
    monitor.check_all_providers()
    changes = [p for p in published if p["topic"] == "provider.health.changed"]
    assert len(changes) == 1


def test_provider_monitor_first_observation_does_not_publish(monkeypatch):
    phm, monitor = _monitor(monkeypatch)
    published = _capture(monkeypatch)
    monitor._last_health = {}
    monitor.check_all_providers()
    assert [p for p in published if p["topic"] == "provider.health.changed"] == []


def _snapshot(running=1):
    return {
        "hostname": "test-host",
        "docker": {"available": True, "containers": [
            {"Names": f"c{i}", "State": "running" if i < running else "exited"}
            for i in range(3)
        ]},
    }


def _worker(monkeypatch, snapshots):
    import core.health_worker as hw
    import core.scanner as scanner
    import core.state as state

    worker = hw.HealthWorker(interval=999)
    monkeypatch.setattr(worker, "_write_state_file", lambda: None)
    monkeypatch.setattr(hw, "record_snapshot", lambda r: None)
    monkeypatch.setattr(scanner, "scan", lambda: snapshots.pop(0))
    monkeypatch.setattr(state, "build_state", lambda **k: None)
    try:
        import core.wireguard_manager as wg
        monkeypatch.setattr(wg, "collect_wg_health_metrics", lambda: {})
    except Exception:
        pass
    return worker


def test_health_worker_publishes_on_genuine_change(monkeypatch):
    published = _capture(monkeypatch)
    worker = _worker(monkeypatch, [_snapshot(1), _snapshot(2), _snapshot(2)])

    worker._sample()  # first observation -> baseline, no publish
    assert [p for p in published if p["topic"] == "infra.health.changed"] == []

    worker._sample()  # container state changed -> publish once
    changes = [p for p in published if p["topic"] == "infra.health.changed"]
    assert len(changes) == 1
    assert changes[0]["source"] == "health_worker"
    assert changes[0]["payload"]["hostname"] == "test-host"

    worker._sample()  # unchanged -> deduped
    changes = [p for p in published if p["topic"] == "infra.health.changed"]
    assert len(changes) == 1
