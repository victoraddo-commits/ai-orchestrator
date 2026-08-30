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
        graph = {"schema_version": 0, "sites": {}}
        nk.save_graph(graph)
        saved = nk.load_graph()
        assert saved["schema_version"] == 1

    def test_atomic_write_creates_bak(self):
        import sys; sys.path.insert(0, "/project/ai-orchestrator")
        os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(self.fake_memory)
        from importlib import reload; import core.network_knowledge as nk; reload(nk)
        graph = {"schema_version": 1, "sites": {}, "tailscale": {}, "tunnel": {}}
        nk.save_graph(graph)
        assert (self.fake_memory / "network_topology.json.bak").exists()

    def test_load_prior_returns_bak(self):
        import sys; sys.path.insert(0, "/project/ai-orchestrator")
        os.environ["AI_ORCHESTRATOR_MEMORY_DIR"] = str(self.fake_memory)
        from importlib import reload; import core.network_knowledge as nk; reload(nk)
        prior_graph = {"schema_version": 1, "sites": {"SITE-A": {"name": "old"}}}
        nk.save_graph(prior_graph)
        fresh = {"schema_version": 1, "sites": {"SITE-A": {"name": "new"}}}
        nk.save_graph(fresh)
        prior = nk.load_prior()
        assert prior["sites"]["SITE-A"]["name"] == "old"
