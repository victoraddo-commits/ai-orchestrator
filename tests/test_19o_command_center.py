"""Tests for Phase 19O: Cerebrum Command Center."""

import os
import sys
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCommandCenter:
    """Cerebrum Command Center mission control."""

    @pytest.fixture(autouse=True)
    def setup(self):
        # Ensure memory/ directory exists for memory system discovery
        self.mem_dir = Path(tempfile.mkdtemp())
        old_mem = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
        os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(self.mem_dir)

        # Create minimal memory files that modules expect
        import json
        (self.mem_dir / "builds.json").write_text(
            json.dumps({"schema_version": 1, "records": []})
        )
        (self.mem_dir / "roadmap.json").write_text(
            json.dumps({"phases": [
                {"id": "19O", "name": "Test", "status": "in_progress"}
            ]})
        )

        # Also need a legal brain DB for trust/knowledge/health queries
        import core.legal_brain.permanent as perm
        self.legal_dir = Path(tempfile.mkdtemp())
        self.db_path = self.legal_dir / "test_cerebrum.db"

        self._orig_get_db_path = perm.get_db_path
        perm.get_db_path = lambda: self.db_path
        perm.init_permanent_store(self.db_path)

        yield

        perm.get_db_path = self._orig_get_db_path
        if old_mem:
            os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = old_mem
        else:
            os.environ.pop("AI_ORCHESTRATOR_MEMORY_DIR", None)

        import shutil
        shutil.rmtree(self.mem_dir, ignore_errors=True)
        shutil.rmtree(self.legal_dir, ignore_errors=True)

    def _make_cc(self):
        from core.cerebrum import CommandCenter
        return CommandCenter(self.db_path, memory_dir=self.mem_dir)

    def test_discover_modules_returns_dict(self):
        """Module discovery returns a non-empty dict."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert isinstance(modules, dict)
        assert len(modules) > 0

    def test_memory_system_available(self):
        """Memory system is always discoverable."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "memory_system" in modules
        mem = modules["memory_system"]
        assert mem["available"] is True
        assert mem["status"] == "healthy"
        assert mem["memory_file_count"] >= 2  # builds.json + roadmap.json

    def test_kai_identity_available(self):
        """Kai identity module is available."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "kai_identity" in modules
        ident = modules["kai_identity"]
        assert ident["available"] is True
        assert "name" in ident

    def test_build_pipeline_tracks_slots(self):
        """Build pipeline reports active/completed/failed counts."""
        import json
        builds = {
            "schema_version": 1,
            "records": [
                {"id": "1", "name": "A", "status": "COMPLETED"},
                {"id": "2", "name": "B", "status": "GENERATING"},
                {"id": "3", "name": "C", "status": "FAILED"},
                {"id": "4", "name": "D", "status": "COMPLETED"},
            ],
        }
        (self.mem_dir / "builds.json").write_text(json.dumps(builds))
        cc = self._make_cc()
        modules = cc.discover_modules()
        bp = modules["build_pipeline"]
        assert bp["total_builds"] == 4
        assert bp["completed"] == 2
        assert bp["active"] == 1
        assert bp["failed"] == 1

    def test_roadmap_module_available(self):
        """Roadmap module reads from memory."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "roadmap" in modules
        rm = modules["roadmap"]
        assert rm["available"] is True
        assert rm["total_phases"] == 1
        assert rm["in_progress"] == 1

    def test_knowledge_engine_available(self):
        """Knowledge engine is discoverable when DB exists."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "knowledge_engine" in modules
        kg = modules["knowledge_engine"]
        assert kg["available"] is True
        assert "entities" in kg

    def test_trust_engine_available(self):
        """Trust engine is discoverable when DB exists."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "trust_engine" in modules
        te = modules["trust_engine"]
        assert te["available"] is True
        assert "sources_scored" in te

    def test_brain_health_available(self):
        """Brain health module is discoverable when DB exists."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "brain_health" in modules
        bh = modules["brain_health"]
        assert bh["available"] is True
        assert "issues_found" in bh

    def test_ai_providers_available(self):
        """AI providers module is discoverable."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "ai_providers" in modules
        prov = modules["ai_providers"]
        assert prov["available"] is True
        assert "providers" in prov

    def test_get_full_status_structure(self):
        """Full status report has expected top-level keys."""
        cc = self._make_cc()
        status = cc.get_full_status()
        assert "timestamp" in status
        assert "cerebrum_version" in status
        assert status["cerebrum_version"] == "19O"
        assert "overall_health" in status
        assert "module_summary" in status
        assert "modules" in status

    def test_module_summary_counts(self):
        """Module summary has correct aggregate counts."""
        cc = self._make_cc()
        status = cc.get_full_status()
        summary = status["module_summary"]
        assert summary["total"] > 0
        assert summary["available"] <= summary["total"]
        assert summary["healthy"] + summary["degraded"] + summary["unavailable"] == summary["total"]

    def test_overall_health_is_valid(self):
        """Overall health is one of the three valid states."""
        cc = self._make_cc()
        status = cc.get_full_status()
        assert status["overall_health"] in ("healthy", "degraded", "unhealthy")

    def test_format_alert_when_healthy(self):
        """No alert when healthy (depends on all modules being clean)."""
        cc = self._make_cc()
        status = cc.get_full_status()
        alert = cc.format_alert(status)
        if status["overall_health"] == "healthy":
            assert alert is None
        else:
            assert isinstance(alert, str)
            assert "Cerebrum Command Center" in alert

    def test_format_alert_when_degraded(self):
        """Alert is generated when a module is degraded."""
        cc = self._make_cc()
        status = cc.get_full_status()
        status["overall_health"] = "degraded"
        status["modules"]["test_module"] = {
            "available": False,
            "status": "degraded",
            "error": "Test degradation",
        }
        alert = cc.format_alert(status)
        assert alert is not None
        assert "DEGRADED" in alert.upper() or "degraded" in alert.lower()
        assert "Test degradation" in alert

    def test_format_dashboard_output(self):
        """Dashboard formatter produces readable text."""
        cc = self._make_cc()
        status = cc.get_full_status()
        dashboard = cc.format_dashboard(status)
        assert isinstance(dashboard, str)
        assert "CEREBRUM COMMAND CENTER" in dashboard
        assert len(dashboard.split("\n")) >= 5

    def test_conversation_module_available(self):
        """Conversation memory module is discoverable."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "conversation" in modules
        conv = modules["conversation"]
        assert conv["available"] is True

    def test_research_sessions_available(self):
        """Research sessions module is discoverable when DB exists."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "research_sessions" in modules
        rs = modules["research_sessions"]
        assert rs["available"] is True

    def test_workspace_module_available(self):
        """Workspace module is discoverable."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        assert "workspace" in modules
        ws = modules["workspace"]
        assert ws["available"] is True

    def test_minimum_modules_present(self):
        """All core cerebrum subsystems are present."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        required = [
            "memory_system",
            "knowledge_engine",
            "trust_engine",
            "brain_health",
            "ai_providers",
            "build_pipeline",
            "kai_identity",
            "roadmap",
            "research_sessions",
            "workspace",
            "conversation",
        ]
        for name in required:
            assert name in modules, f"Missing module: {name}"

    def test_factory_function(self):
        """Factory function returns a CommandCenter."""
        from core.cerebrum import get_command_center
        cc = get_command_center(self.db_path, memory_dir=self.mem_dir)
        from core.cerebrum.command_center import CommandCenter
        assert isinstance(cc, CommandCenter)

    def test_providers_have_expected_fields(self):
        """Each provider entry has the expected structure."""
        cc = self._make_cc()
        modules = cc.discover_modules()
        providers = modules["ai_providers"].get("providers", [])
        for p in providers:
            assert "name" in p
            assert "enabled" in p
            assert "health_status" in p
