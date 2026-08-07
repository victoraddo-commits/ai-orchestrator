"""Tests for AI Agent Registry.

Verifies: agent CRUD, enable/disable, bootstrapping, benchmark recording,
cost/performance history, agent stats."""

import os
import sys
import json
import time
import tempfile
from pathlib import Path
import pytest

# Use a test storage path
sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DIR = Path(tempfile.gettempdir()) / "agent_registry_test"
os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(TEST_DIR)


@pytest.fixture(autouse=True)
def setup():
    """Clean test storage before each test."""
    import core.ai.agent_registry as reg
    reg.STORAGE_PATH = TEST_DIR / "agents.json"

    if reg.STORAGE_PATH.exists():
        reg.STORAGE_PATH.unlink()
    if TEST_DIR.exists():
        for f in TEST_DIR.iterdir():
            if f.is_file():
                f.unlink()
    yield
    if reg.STORAGE_PATH.exists():
        reg.STORAGE_PATH.unlink()


class TestAgentCRUD:
    """Agent create, read, update, delete."""

    def test_register_and_get(self):
        """Registering an agent makes it retrievable by ID."""
        from core.ai.agent_registry import register_agent, get_agent

        agent = register_agent(
            agent_id="qwen4_text",
            name="Qwen4 Text",
            provider_key="qwen4_text",
            model_name="Qwen3-32B-FP8",
            model_version="vLLM RunPod",
            gpu_type="RTX PRO 6000",
            gpu_memory_gb=96,
            cost_per_hour=0.99,
            capabilities=["text_task"],
            fallback_chain=["gemini", "deepseek_native_flash"],
            description="Primary text generator on RunPod A",
        )

        assert agent["id"] == "qwen4_text"
        assert agent["name"] == "Qwen4 Text"
        assert agent["status"] == "active"
        assert "text_task" in agent["capabilities"]

        retrieved = get_agent("qwen4_text")
        assert retrieved is not None
        assert retrieved["id"] == "qwen4_text"

    def test_register_updates_existing(self):
        """Registering an existing agent ID updates its metadata."""
        from core.ai.agent_registry import register_agent, get_agent

        register_agent(
            agent_id="test_agent",
            name="Old Name",
            provider_key="foo",
            model_name="old-model",
        )

        register_agent(
            agent_id="test_agent",
            name="New Name",
            provider_key="bar",
            model_name="new-model",
            capabilities=["text_task"],
        )

        updated = get_agent("test_agent")
        assert updated["name"] == "New Name"
        assert updated["provider_key"] == "bar"
        assert updated["model_name"] == "new-model"
        assert updated["capabilities"] == ["text_task"]
        assert updated["status"] == "active"  # preserved

    def test_list_agents(self):
        """List returns all agents sorted by name."""
        from core.ai.agent_registry import register_agent, list_agents

        register_agent(agent_id="b", name="Agent B", provider_key="b", model_name="b")
        register_agent(agent_id="c", name="Agent C", provider_key="c", model_name="c")
        register_agent(agent_id="a", name="Agent A", provider_key="a", model_name="a")

        agents = list_agents()
        assert len(agents) == 3
        assert agents[0]["name"] == "Agent A"
        assert agents[1]["name"] == "Agent B"
        assert agents[2]["name"] == "Agent C"

    def test_list_agents_filtered_by_status(self):
        """List can filter by status."""
        from core.ai.agent_registry import register_agent, list_agents, disable_agent

        register_agent(agent_id="enabled_agent", name="Enabled", provider_key="e", model_name="e")
        register_agent(agent_id="disabled_agent", name="Disabled", provider_key="d", model_name="d")
        disable_agent("disabled_agent")

        active = list_agents(status="active")
        disabled = list_agents(status="disabled")

        assert len(active) == 1
        assert active[0]["id"] == "enabled_agent"
        assert len(disabled) == 1
        assert disabled[0]["id"] == "disabled_agent"

    def test_get_nonexistent_returns_none(self):
        """Getting a nonexistent agent returns None."""
        from core.ai.agent_registry import get_agent
        assert get_agent("nonexistent") is None

    def test_delete_agent(self):
        """Deleting an agent removes it from the registry."""
        from core.ai.agent_registry import register_agent, get_agent, delete_agent

        register_agent(agent_id="temp", name="Temporary", provider_key="t", model_name="t")
        assert get_agent("temp") is not None

        assert delete_agent("temp") is True
        assert get_agent("temp") is None

    def test_delete_nonexistent_returns_false(self):
        """Delete on a nonexistent agent returns False."""
        from core.ai.agent_registry import delete_agent
        assert delete_agent("bogus") is False


class TestAgentEnableDisable:
    """Agent enable/disable/maintenance states."""

    def test_enable_and_disable(self):
        """Enabling and disabling toggles status."""
        from core.ai.agent_registry import register_agent, get_agent, enable_agent, disable_agent

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        assert get_agent("test")["status"] == "active"

        disable_agent("test")
        assert get_agent("test")["status"] == "disabled"

        enable_agent("test")
        assert get_agent("test")["status"] == "active"

    def test_set_maintenance(self):
        """Maintenance status is distinct from disabled."""
        from core.ai.agent_registry import register_agent, get_agent, set_maintenance

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        set_maintenance("test")
        assert get_agent("test")["status"] == "maintenance"

    def test_enable_nonexistent_returns_false(self):
        """Enable on a nonexistent agent returns False."""
        from core.ai.agent_registry import enable_agent, disable_agent
        assert enable_agent("bogus") is False
        assert disable_agent("bogus") is False


class TestBenchmarks:
    """Benchmark recording."""

    def test_record_benchmark(self):
        """Recording benchmarks stores latency, accuracy, tool success rate."""
        from core.ai.agent_registry import register_agent, get_agent, record_benchmark

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        record_benchmark("test", latency_ms=250, accuracy_score=0.95, tool_use_success_rate=0.88)

        agent = get_agent("test")
        bm = agent["benchmarks"]
        assert bm["latency_ms"] == 250
        assert bm["accuracy_score"] == 0.95
        assert bm["tool_use_success_rate"] == 0.88
        assert bm["last_benchmark_at"] is not None


class TestCostAndPerformance:
    """Cost and performance history tracking."""

    def test_record_cost(self):
        """Recording cost adds an entry to cost_history."""
        from core.ai.agent_registry import register_agent, get_agent, record_cost

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        record_cost("test", "run-001", cost_usd=0.015, tokens_in=500, tokens_out=200,
                    task_type="classification", project="test-project")

        agent = get_agent("test")
        assert len(agent["cost_history"]) == 1
        entry = agent["cost_history"][0]
        assert entry["run_id"] == "run-001"
        assert entry["cost_usd"] == 0.015
        assert entry["tokens_in"] == 500
        assert entry["tokens_out"] == 200
        assert entry["task_type"] == "classification"

    def test_record_performance(self):
        """Recording performance adds an entry to performance_history."""
        from core.ai.agent_registry import register_agent, get_agent, record_performance

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        record_performance("test", "run-001", latency_ms=320, success=True,
                           task_type="coding", error_type="")

        agent = get_agent("test")
        assert len(agent["performance_history"]) == 1
        entry = agent["performance_history"][0]
        assert entry["run_id"] == "run-001"
        assert entry["latency_ms"] == 320
        assert entry["success"] is True

    def test_get_cost_history_newest_first(self):
        """Cost history returns newest entries first."""
        from core.ai.agent_registry import register_agent, get_cost_history, record_cost

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        record_cost("test", "run-1", cost_usd=0.01, tokens_in=100, tokens_out=50)
        record_cost("test", "run-2", cost_usd=0.02, tokens_in=200, tokens_out=100)

        entries = get_cost_history("test", limit=10)
        assert len(entries) == 2
        assert entries[0]["run_id"] == "run-2"  # newest first

    def test_get_performance_history_newest_first(self):
        """Performance history returns newest entries first."""
        from core.ai.agent_registry import register_agent, get_performance_history, record_performance

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        record_performance("test", "run-1", 100, True)
        record_performance("test", "run-2", 200, False, error_type="timeout")

        entries = get_performance_history("test", limit=10)
        assert len(entries) == 2
        assert entries[0]["run_id"] == "run-2"


class TestAgentStats:
    """Aggregate stats computation."""

    def test_get_agent_stats(self):
        """Stats include success rate, avg latency, total cost."""
        from core.ai.agent_registry import register_agent, get_agent_stats
        from core.ai.agent_registry import record_performance, record_cost

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        record_performance("test", "r1", 100, True)
        record_performance("test", "r2", 200, True)
        record_performance("test", "r3", 300, False)
        record_cost("test", "r1", 0.01, 100, 50)
        record_cost("test", "r2", 0.02, 200, 100)
        record_cost("test", "r3", 0.03, 300, 150)

        stats = get_agent_stats("test")
        assert stats["total_runs"] == 3
        assert stats["successful_runs"] == 2
        assert stats["success_rate"] == pytest.approx(0.6667, abs=0.01)
        assert stats["avg_latency_ms"] == 200
        assert stats["total_cost_usd"] == pytest.approx(0.06)

    def test_get_agent_stats_empty_returns_zeros(self):
        """Stats for agent with no history returns zeros, not errors."""
        from core.ai.agent_registry import register_agent, get_agent_stats

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        stats = get_agent_stats("test")
        assert stats["total_runs"] == 0
        assert stats["success_rate"] == 0
        assert stats["avg_latency_ms"] == 0
        assert stats["total_cost_usd"] == 0

    def test_get_stats_nonexistent_returns_empty(self):
        """Stats for nonexistent agent returns empty dict."""
        from core.ai.agent_registry import get_agent_stats
        assert get_agent_stats("bogus") == {}


class TestFallbackChain:
    """Fallback chain management."""

    def test_set_and_get_fallback_chain(self):
        """Setting a fallback chain is retrievable."""
        from core.ai.agent_registry import register_agent, get_agent, set_fallback_chain, get_fallback_chain

        register_agent(agent_id="test", name="Test", provider_key="t", model_name="t")
        chain = ["backup1", "backup2", "backup3"]
        set_fallback_chain("test", chain)
        assert get_agent("test")["fallback_chain"] == chain

    def test_get_fallback_chain_filters_disabled(self):
        """Fallback chain getter filters out disabled agents."""
        from core.ai.agent_registry import (
            register_agent, get_fallback_chain, disable_agent, register_agent as reg
        )

        reg(agent_id="test", name="Test", provider_key="t", model_name="t")
        reg(agent_id="backup1", name="Backup 1", provider_key="b1", model_name="b1")
        reg(agent_id="backup2", name="Backup 2", provider_key="b2", model_name="b2")

        from core.ai.agent_registry import set_fallback_chain
        set_fallback_chain("test", ["backup1", "backup2"])

        assert get_fallback_chain("test") == ["backup1", "backup2"]

        disable_agent("backup1")
        chain = get_fallback_chain("test")
        assert "backup1" not in chain
        assert "backup2" in chain


class TestBootstrap:
    """Default agent bootstrapping."""

    def test_bootstrap_registers_default_agents(self):
        """Bootstrap creates default agents that match existing providers."""
        from core.ai.agent_registry import list_agents, bootstrap_default_agents

        agents_before = list_agents()
        count = bootstrap_default_agents()
        agents_after = list_agents()

        # bootstrap_default_agents only creates agents whose provider exists
        # in the provider registry.  The number depends on what's registered.
        # Just verify it doesn't crash and count is consistent.
        total_existing = len(agents_before) + count
        assert len(agents_after) == total_existing
        assert count >= 0

    def test_bootstrap_is_idempotent(self):
        """Bootstrapping twice doesn't duplicate agents."""
        from core.ai.agent_registry import list_agents, bootstrap_default_agents

        bootstrap_default_agents()
        first_count = len(list_agents())

        bootstrap_default_agents()
        second_count = len(list_agents())

        assert second_count == first_count


class TestTestAgent:
    """Agent health test endpoint (tests the function shape, not real providers)."""

    def test_test_agent_nonexistent(self):
        """Testing a nonexistent agent returns error."""
        from core.ai.agent_registry import test_agent
        result = test_agent("nonexistent")
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    def test_test_agent_without_provider_key(self):
        """Agent without a provider_key can't be tested."""
        from core.ai.agent_registry import register_agent, test_agent

        register_agent(agent_id="orphan", name="Orphan", provider_key="", model_name="x")
        result = test_agent("orphan")
        assert result["ok"] is False


class TestPersistence:
    """Agent data survives re-loading."""

    def test_persistence_across_reloads(self):
        """Agents saved to disk survive module re-import."""
        from core.ai.agent_registry import register_agent, list_agents

        register_agent(agent_id="persistent", name="Persistent", provider_key="p", model_name="p")

        # Simulate reload: re-load from disk
        from core.ai.agent_registry import _load
        data = _load()
        assert "persistent" in data.get("agents", {})

        loaded = data["agents"]["persistent"]
        assert loaded["name"] == "Persistent"
        assert loaded["id"] == "persistent"
