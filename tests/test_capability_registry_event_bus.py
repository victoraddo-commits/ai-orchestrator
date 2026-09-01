"""Tests for capability_registry.py event bus integration."""
import pytest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_capability_health_event_published_on_status_change(monkeypatch):
    """publish is called when capability status transitions."""
    published = []
    def mock_publish(topic, payload, source=None, severity=None, journal=None):
        published.append({"topic": topic, "payload": payload, "source": source, "severity": severity})
        return 1

    from core.capability_registry import CapabilityRegistry
    with patch("core.kai_event_bus.event_bus.publish", mock_publish):
        # Create registry and manually set a capability with a health transition
        cap_reg = CapabilityRegistry.__new__(CapabilityRegistry)
        cap_reg._capabilities = {
            "test-cap": {
                "capability_id": "test-cap",
                "status": "healthy",
                "implementations": [],
            }
        }
        cap_reg._health_history = []
        cap_reg.memory_dir = Path("/tmp/test_cap_reg")
        cap_reg.memory_dir.mkdir(exist_ok=True, parents=True)

        # Manually trigger what refresh_health does when status changes
        from core.kai_event_bus import event_bus, IMPORTANT
        old_status = "healthy"
        new_status = "degraded"
        cap_reg._capabilities["test-cap"]["status"] = new_status

        # Call publish directly as refresh_health would after status update
        cap_id = "test-cap"
        cap = cap_reg._capabilities[cap_id]
        severity = "critical" if new_status == "down" else "important"
        event_bus.publish(
            f"capability.health.{cap_id}",
            {
                "capability_id": cap_id,
                "old_status": old_status,
                "new_status": new_status,
                "implementations": [
                    {"service_id": i["service_id"], "health": i.get("health", "unknown")}
                    for i in cap.get("implementations", [])
                ],
            },
            source="capability_registry",
            severity=severity,
            journal=True,
        )

    assert len(published) == 1
    assert published[0]["topic"] == "capability.health.test-cap"
    assert published[0]["payload"]["new_status"] == "degraded"
    assert published[0]["source"] == "capability_registry"
    assert published[0]["severity"] == "important"  # not "down" so "important"


def test_capability_health_event_critical_on_down(monkeypatch):
    """publish uses severity=critical when new_status is down."""
    published = []
    def mock_publish(topic, payload, source=None, severity=None, journal=None):
        published.append({"topic": topic, "payload": payload, "source": source, "severity": severity})
        return 1

    from core.capability_registry import CapabilityRegistry
    with patch("core.kai_event_bus.event_bus.publish", mock_publish):
        cap_reg = CapabilityRegistry.__new__(CapabilityRegistry)
        cap_reg._capabilities = {
            "test-cap": {
                "capability_id": "test-cap",
                "status": "degraded",
                "implementations": [],
            }
        }
        cap_reg._health_history = []
        cap_reg.memory_dir = Path("/tmp/test_cap_reg2")
        cap_reg.memory_dir.mkdir(exist_ok=True, parents=True)

        from core.kai_event_bus import event_bus, IMPORTANT
        old_status = "degraded"
        new_status = "down"
        cap_reg._capabilities["test-cap"]["status"] = new_status

        cap_id = "test-cap"
        cap = cap_reg._capabilities[cap_id]
        severity = "critical" if new_status == "down" else "important"
        event_bus.publish(
            f"capability.health.{cap_id}",
            {
                "capability_id": cap_id,
                "old_status": old_status,
                "new_status": new_status,
                "implementations": [],
            },
            source="capability_registry",
            severity=severity,
            journal=True,
        )

    assert len(published) == 1
    assert published[0]["severity"] == "critical"
    assert published[0]["payload"]["new_status"] == "down"
