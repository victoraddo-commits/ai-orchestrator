"""
Tests for core.capability_registry_discovery.
"""
import json
import pytest
import tempfile
from pathlib import Path

# Module-scoped import to allow running directly with: python -m pytest tests/...
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.capability_registry_discovery import (
    get_explicit_mapping,
    detect_capability_for_service,
    link_services_to_capabilities,
    NAME_CAPABILITY_MAP,
    PORT_CAPABILITY_MAP,
)


# ----------------------------------------------------------------------------------------
# get_explicit_mapping
# ----------------------------------------------------------------------------------------

class TestGetExplicitMapping:
    """Tests for get_explicit_mapping()."""

    def test_file_missing_returns_empty_dict(self, tmp_path, monkeypatch):
        """When the mapping file does not exist, return {}."""
        monkeypatch.setattr(
            "core.capability_registry_discovery.MAPPING_FILE",
            tmp_path / "nonexistent.json",
        )
        assert get_explicit_mapping() == {}

    def test_underscore_keys_skipped(self, tmp_path, monkeypatch):
        """Keys starting with _ are silently skipped."""
        mapping_file = tmp_path / "mapping.json"
        monkeypatch.setattr(
            "core.capability_registry_discovery.MAPPING_FILE",
            mapping_file,
        )
        mapping_file.write_text(json.dumps({
            "_comment": "this is a comment",
            "_disabled": "telegram-bots",
            "kai-telegram-core": "telegram-bots",
        }))
        result = get_explicit_mapping()
        assert "_comment" not in result
        assert "_disabled" not in result
        assert result["kai-telegram-core"] == "telegram-bots"

    def test_normal_mapping_returned(self, tmp_path, monkeypatch):
        """A normal mapping file is parsed and returned as-is (minus _ keys)."""
        mapping_file = tmp_path / "mapping.json"
        monkeypatch.setattr(
            "core.capability_registry_discovery.MAPPING_FILE",
            mapping_file,
        )
        data = {
            "svc-a": "cap-1",
            "svc-b": "cap-2",
            "_internal": "ignored",
        }
        mapping_file.write_text(json.dumps(data))
        result = get_explicit_mapping()
        assert result == {"svc-a": "cap-1", "svc-b": "cap-2"}
        assert "_internal" not in result

    def test_invalid_json_returns_empty_dict(self, tmp_path, monkeypatch):
        """A malformed JSON file is logged and returns {}."""
        mapping_file = tmp_path / "bad.json"
        monkeypatch.setattr(
            "core.capability_registry_discovery.MAPPING_FILE",
            mapping_file,
        )
        mapping_file.write_text("{ invalid json }")
        result = get_explicit_mapping()
        assert result == {}


# ----------------------------------------------------------------------------------------
# detect_capability_for_service
# ----------------------------------------------------------------------------------------

class TestDetectCapabilityForService:
    """Tests for detect_capability_for_service()."""

    # --- Explicit mapping takes priority over name and port ---

    def test_explicit_mapping_priority_over_name(self, tmp_path, monkeypatch):
        """When a service_id appears in the explicit mapping, name patterns are skipped."""
        mapping_file = tmp_path / "mapping.json"
        monkeypatch.setattr(
            "core.capability_registry_discovery.MAPPING_FILE",
            mapping_file,
        )
        mapping_file.write_text(json.dumps({"my-telegram-svc": "custom-cap"}))

        svc = {"id": "my-telegram-svc", "name": "telegram service", "port": 8094}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == "custom-cap"
        assert auto_detected is False

    def test_explicit_mapping_priority_over_port(self, tmp_path, monkeypatch):
        """When a service_id appears in the explicit mapping, port detection is skipped."""
        mapping_file = tmp_path / "mapping.json"
        monkeypatch.setattr(
            "core.capability_registry_discovery.MAPPING_FILE",
            mapping_file,
        )
        mapping_file.write_text(json.dumps({"svc-at-8095": "override-cap"}))

        svc = {"id": "svc-at-8095", "name": "money service", "port": 8095}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == "override-cap"
        assert auto_detected is False

    # --- Name pattern tests ---

    @pytest.mark.parametrize("pattern,expected_cap", NAME_CAPABILITY_MAP.items())
    def test_name_patterns_match_id(self, pattern, expected_cap):
        """Each named substring in NAME_CAPABILITY_MAP matches when it appears in the service id."""
        svc = {"id": f"my-{pattern}-service", "name": "some other service"}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == expected_cap
        assert auto_detected is True

    @pytest.mark.parametrize("pattern,expected_cap", NAME_CAPABILITY_MAP.items())
    def test_name_patterns_match_name_field(self, pattern, expected_cap):
        """Each named substring in NAME_CAPABILITY_MAP matches when it appears in the service name."""
        svc = {"id": "generic-service", "name": f"my {pattern} wrapper"}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == expected_cap
        assert auto_detected is True

    def test_name_pattern_case_insensitive(self):
        """Name pattern matching is case-insensitive."""
        svc = {"id": "KAI-TELEGRAM-CORE", "name": "TELEGRAM BRIDGE"}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == "telegram-bots"
        assert auto_detected is True

    # --- Port detection tests ---

    @pytest.mark.parametrize("port,expected_cap", PORT_CAPABILITY_MAP.items())
    def test_port_single_int(self, port, expected_cap):
        """Each port in PORT_CAPABILITY_MAP is detected when port is an int."""
        svc = {"id": "generic-svc", "name": "generic", "port": port}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == expected_cap
        assert auto_detected is True

    @pytest.mark.parametrize("port,expected_cap", PORT_CAPABILITY_MAP.items())
    def test_port_single_list(self, port, expected_cap):
        """Each port in PORT_CAPABILITY_MAP is detected when port is a single-item list."""
        svc = {"id": "generic-svc", "name": "generic", "port": [port]}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == expected_cap
        assert auto_detected is True

    def test_port_list_returns_first_match(self):
        """When port is a list, the first matching port wins."""
        svc = {"id": "multi-port-svc", "name": "multi", "port": [8443, 8094]}
        cap_id, auto_detected = detect_capability_for_service(svc)
        # 8443 → telegram-bots (checked first)
        assert cap_id == "telegram-bots"
        assert auto_detected is True

    def test_port_list_no_match_returns_unknown(self):
        """When port is a list with no matching ports, fall back to unknown."""
        svc = {"id": "no-match-svc", "name": "no match", "port": [1234, 5678]}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == "unknown"
        assert auto_detected is True

    # --- Fallback ---

    def test_no_match_returns_unknown(self):
        """A service with no matching pattern/port returns 'unknown'."""
        svc = {"id": "completely-generic", "name": "generic thing"}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == "unknown"
        assert auto_detected is True

    def test_missing_id_and_name_falls_back(self):
        """A service with no id/name and no detectable port falls back to unknown."""
        svc = {"port": 9999}  # 9999 is not in PORT_CAPABILITY_MAP
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == "unknown"
        assert auto_detected is True

    def test_no_port_field_falls_back(self):
        """A service with no port field falls back to unknown."""
        svc = {"id": "generic-svc", "name": "generic"}
        cap_id, auto_detected = detect_capability_for_service(svc)
        assert cap_id == "unknown"
        assert auto_detected is True


# ----------------------------------------------------------------------------------------
# link_services_to_capabilities
# ----------------------------------------------------------------------------------------

class TestLinkServicesToCapabilities:
    """Tests for link_services_to_capabilities()."""

    def test_unknown_capability_not_seeded(self, tmp_path):
        """A service with no matching pattern is skipped — no 'unknown' garbage seeded."""
        from core.capability_registry import CapabilityRegistry

        # Mock ServiceRegistry with a single generic service that matches nothing
        class MockSvcReg:
            def list_services(self):
                # No Telegram, kai-*, proxdash, etc. — will fall through to unknown
                return {"random-svc": {"id": "random-svc", "name": "Random Service", "status": "healthy"}}

        cap_reg = CapabilityRegistry(memory_dir=tmp_path)
        import core.service_registry
        orig = core.service_registry.ServiceRegistry.get_instance
        core.service_registry.ServiceRegistry.get_instance = classmethod(lambda cls: MockSvcReg())

        try:
            link_services_to_capabilities(cap_reg)
        finally:
            core.service_registry.ServiceRegistry.get_instance = orig

        # No capability should be created for an unmatched service
        assert "unknown" not in cap_reg._capabilities
        # The registry should be empty
        assert len(cap_reg._capabilities) == 0

    def test_explicit_mapping_creates_primary_role(self, tmp_path, monkeypatch):
        """A service mapped explicitly gets role=primary, auto_detected=False."""
        mapping_file = tmp_path / "mapping.json"
        monkeypatch.setattr(
            "core.capability_registry_discovery.MAPPING_FILE",
            mapping_file,
        )
        mapping_file.write_text(json.dumps({"telegram-svc": "telegram-bots"}))

        class MockSvcReg:
            def list_services(self):
                return {"telegram-svc": {"id": "telegram-svc", "name": "Telegram", "owner": "team-x", "status": "healthy"}}

        from core.capability_registry import CapabilityRegistry
        import core.service_registry
        orig = core.service_registry.ServiceRegistry.get_instance
        core.service_registry.ServiceRegistry.get_instance = classmethod(lambda cls: MockSvcReg())

        try:
            cap_reg = CapabilityRegistry(memory_dir=tmp_path)
            link_services_to_capabilities(cap_reg)
        finally:
            core.service_registry.ServiceRegistry.get_instance = orig

        assert "telegram-bots" in cap_reg._capabilities
        impls = cap_reg._capabilities["telegram-bots"]["implementations"]
        assert any(
            i["service_id"] == "telegram-svc"
            and i["role"] == "primary"
            and i["auto_detected"] is False
            and i["override"] is True
            for i in impls
        )

    def test_duplicate_service_not_linked_twice(self, tmp_path, monkeypatch):
        """Linking the same service twice does not create duplicate implementation entries."""
        mapping_file = tmp_path / "mapping.json"
        monkeypatch.setattr(
            "core.capability_registry_discovery.MAPPING_FILE",
            mapping_file,
        )
        mapping_file.write_text(json.dumps({}))

        class MockSvcReg:
            def list_services(self):
                # telegram-svc maps to telegram-bots via NAME_CAPABILITY_MAP
                return {"telegram-svc": {"id": "telegram-svc", "name": "Telegram Bot", "status": "healthy"}}

        from core.capability_registry import CapabilityRegistry
        import core.service_registry
        orig = core.service_registry.ServiceRegistry.get_instance
        core.service_registry.ServiceRegistry.get_instance = classmethod(lambda cls: MockSvcReg())

        try:
            cap_reg = CapabilityRegistry(memory_dir=tmp_path)
            link_services_to_capabilities(cap_reg)
            link_services_to_capabilities(cap_reg)  # call again
        finally:
            core.service_registry.ServiceRegistry.get_instance = orig

        impls = cap_reg._capabilities["telegram-bots"]["implementations"]
        count = sum(1 for i in impls if i["service_id"] == "telegram-svc")
        assert count == 1, "Service should only be linked once, not duplicated"
