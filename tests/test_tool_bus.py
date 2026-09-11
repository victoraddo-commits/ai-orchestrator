"""Tests for the rebuilt Tool Bus + Tool Risk + Mission modules."""

import pytest
from unittest.mock import patch, MagicMock


class TestToolDescriptors:
    def test_descriptors_list_exists(self):
        from core.kai.tool_descriptors import TOOL_DESCRIPTORS
        assert isinstance(TOOL_DESCRIPTORS, list)
        assert len(TOOL_DESCRIPTORS) > 0

    def test_each_descriptor_has_required_fields(self):
        from core.kai.tool_descriptors import TOOL_DESCRIPTORS
        required = {"name", "description", "input_schema", "output_schema", "risk_level", "capabilities_required"}
        for desc in TOOL_DESCRIPTORS:
            missing = required - set(desc.keys())
            assert not missing, f"{desc['name']} missing: {missing}"

    def test_no_duplicate_names(self):
        from core.kai.tool_descriptors import TOOL_DESCRIPTORS
        names = [d["name"] for d in TOOL_DESCRIPTORS]
        assert len(names) == len(set(names))


class TestToolBus:
    def setup_method(self):
        import core.kai.tool_bus as tb
        tb.ToolBus._instance = None
        tb._bus = None

    def test_singleton(self):
        from core.kai.tool_bus import ToolBus
        a = ToolBus()
        b = ToolBus()
        assert a is b

    def test_register_and_get(self):
        from core.kai.tool_bus import ToolBus
        bus = ToolBus()
        bus.register("test.tool", "A test tool", risk_level="low")
        tool = bus.get("test.tool")
        assert tool is not None
        assert tool["name"] == "test.tool"
        assert tool["risk_level"] == "low"

    def test_unregister(self):
        from core.kai.tool_bus import ToolBus
        bus = ToolBus()
        bus.register("rm.me", "temp")
        assert bus.has_tool("rm.me")
        assert bus.unregister("rm.me") is True
        assert not bus.has_tool("rm.me")
        assert bus.unregister("rm.me") is False

    def test_list_tools(self):
        from core.kai.tool_bus import ToolBus
        bus = ToolBus()
        bus.register("a.tool", "A")
        bus.register("b.tool", "B")
        names = bus.list_tools()
        assert "a.tool" in names
        assert "b.tool" in names

    def test_invoke_no_handler(self):
        from core.kai.tool_bus import ToolBus
        bus = ToolBus()
        bus.register("stub.tool", "stub", risk_level="low")
        result = bus.invoke("stub.tool", {})
        assert result["status"] == "ok"

    def test_invoke_with_handler(self):
        from core.kai.tool_bus import ToolBus
        bus = ToolBus()
        handler = lambda params: {"echo": params.get("msg")}
        bus.register("echo.tool", "echo", handler=handler, risk_level="low")
        result = bus.invoke("echo.tool", {"msg": "hello"})
        assert result == {"echo": "hello"}

    def test_invoke_unknown_raises(self):
        from core.kai.tool_bus import ToolBus
        bus = ToolBus()
        with pytest.raises(KeyError):
            bus.invoke("nonexistent", {})

    def test_module_level_functions(self):
        import core.kai.tool_bus as tb
        tb.register("mod.tool", "module level test", risk_level="low")
        assert tb.has_tool("mod.tool")
        assert tb.get("mod.tool")["name"] == "mod.tool"
        assert "mod.tool" in tb.list_tools()
        tb.unregister("mod.tool")

    def test_register_builtin_handlers(self):
        from core.kai.tool_bus import ToolBus, _register_builtin_handlers
        bus = ToolBus()
        _register_builtin_handlers(bus)
        assert bus.has_tool("inventory.get")
        assert bus.has_tool("vault.request")
        assert bus.has_tool("mission.create")


class TestToolRisk:
    def test_low_risk_tool(self):
        from core.kai.tool_risk import check_risk
        result = check_risk("inventory.get")
        assert result["risk_level"] == "low"
        assert result["requires_approval"] is False
        assert result["blocking"] is False

    def test_critical_risk_tool(self):
        from core.kai.tool_risk import check_risk
        result = check_risk("vault.request")
        assert result["risk_level"] == "critical"
        assert result["requires_approval"] is True
        assert result["blocking"] is True

    def test_vault_high_risk_secret_path(self):
        from core.kai.tool_risk import check_risk
        result = check_risk("vault.request", {"secret_path": "prod/api_key"})
        assert result["risk_level"] == "critical"
        assert result["blocking"] is True

    def test_unknown_tool_defaults_low(self):
        from core.kai.tool_risk import check_risk
        result = check_risk("totally.unknown.tool")
        assert result["risk_level"] == "low"

    def test_get_risk_for_tools(self):
        from core.kai.tool_risk import get_risk_for_tools
        results = get_risk_for_tools(["inventory.get", "vault.request"])
        assert "inventory.get" in results
        assert "vault.request" in results
        assert results["inventory.get"]["risk_level"] == "low"
        assert results["vault.request"]["risk_level"] == "critical"

    def test_medium_risk_tool(self):
        from core.kai.tool_risk import check_risk
        result = check_risk("docker.start")
        assert result["risk_level"] == "medium"
        assert result["requires_approval"] is False

    def test_high_risk_mission_steer(self):
        from core.kai.tool_risk import check_risk
        result = check_risk("mission.steer")
        assert result["risk_level"] == "high"
        assert result["requires_approval"] is True
        assert result["blocking"] is False


class TestMissionEngine:
    @patch("core.kai.mission_store.load", return_value=[])
    @patch("core.kai.mission_store.save")
    def test_compute_drift_score_identical(self, mock_save, mock_load):
        from core.kai.mission_engine import compute_drift_score
        assert compute_drift_score("fix the bug", "fix the bug") == 0.0

    @patch("core.kai.mission_store.load", return_value=[])
    @patch("core.kai.mission_store.save")
    def test_compute_drift_score_different(self, mock_save, mock_load):
        from core.kai.mission_engine import compute_drift_score
        score = compute_drift_score("deploy the app", "refactor the database")
        assert 0.0 < score <= 1.0

    @patch("core.kai.mission_store.load", return_value=[])
    @patch("core.kai.mission_store.save")
    def test_create_mission(self, mock_save, mock_load):
        from core.kai.mission_engine import create_mission
        m = create_mission(objective="test mission")
        assert m["objective"] == "test mission"
        assert m["status"] == "proposed"
        assert m["id"]
