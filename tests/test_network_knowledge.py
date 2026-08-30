# tests/test_network_knowledge.py
import pytest, os, tempfile, shutil
from pathlib import Path

class TestNetworkKnowledge:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.fake_memory = Path(self.tmp)

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def test_schema_version_set_on_save(self):
        import sys; sys.path.insert(0, "/project/ai-orchestrator")
        os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(self.fake_memory)
        from importlib import reload; import core.network_knowledge as nk; reload(nk)
        graph = {
            "schema_version": 0, "sites": {},
            "tailscale": {"peers": {}, "subnet_routes": {}},
            "tunnel": {"status": "UNKNOWN", "a_to_b_latency_ms": None,
                       "b_to_a_latency_ms": None, "packet_loss_pct": None, "last_test": None},
        }
        nk.save_graph(graph)
        saved = nk.load_graph()
        assert saved["schema_version"] == 1

    def test_atomic_write_creates_bak(self):
        import sys; sys.path.insert(0, "/project/ai-orchestrator")
        os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(self.fake_memory)
        from importlib import reload; import core.network_knowledge as nk; reload(nk)
        graph = {
            "schema_version": 1, "sites": {},
            "tailscale": {"peers": {}, "subnet_routes": {}},
            "tunnel": {"status": "UNKNOWN", "a_to_b_latency_ms": None,
                       "b_to_a_latency_ms": None, "packet_loss_pct": None, "last_test": None},
        }
        nk.save_graph(graph)
        assert (self.fake_memory / "network_topology.json.bak").exists()

    def test_load_prior_returns_bak(self):
        import sys; sys.path.insert(0, "/project/ai-orchestrator")
        os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(self.fake_memory)
        from importlib import reload; import core.network_knowledge as nk; reload(nk)
        prior_graph = {
            "schema_version": 1, "sites": {"SITE-A": {"name": "old"}},
            "tailscale": {"peers": {}, "subnet_routes": {}},
            "tunnel": {"status": "UNKNOWN", "a_to_b_latency_ms": None,
                       "b_to_a_latency_ms": None, "packet_loss_pct": None, "last_test": None},
        }
        nk.save_graph(prior_graph)
        fresh = {
            "schema_version": 1, "sites": {"SITE-A": {"name": "new"}},
            "tailscale": {"peers": {}, "subnet_routes": {}},
            "tunnel": {"status": "UNKNOWN", "a_to_b_latency_ms": None,
                       "b_to_a_latency_ms": None, "packet_loss_pct": None, "last_test": None},
        }
        nk.save_graph(fresh)
        prior = nk.load_prior()
        assert prior["sites"]["SITE-A"]["name"] == "old"

    def test_load_graph_returns_empty_when_no_file(self):
        import sys; sys.path.insert(0, "/project/ai-orchestrator")
        os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(self.fake_memory)
        from importlib import reload; import core.network_knowledge as nk; reload(nk)
        graph = nk.load_graph()
        assert graph["schema_version"] == 1
        assert graph["sites"] == {}
        assert graph["tailscale"] == {"peers": {}, "subnet_routes": {}}
